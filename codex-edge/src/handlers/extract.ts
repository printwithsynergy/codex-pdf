/**
 * `POST /v1/extract/stream` handler.
 *
 * Two-phase mode: cached payloads cover Phase 1 + Phase 2 events.
 * Granular mode (`?granular=1`): cached payloads cover phase1 +
 * phase2_complete only — the four intermediate pikepdf events are
 * always relayed live (they're cheap to compute on cache replay
 * because phase2_complete already contains the merged data).
 *
 * Multipart uploads bypass the edge cache and go straight to origin
 * (we'd need to hash the upload to look up by sha, which adds 5-50 ms
 * for nothing on a cache miss). Hash-keyed JSON requests do hit edge.
 */

import type { Env } from "../env";
import { cacheKey } from "../cache_key";
import { buildSseEvent } from "../sse_tee";

export async function handleExtractStream(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const granular = url.searchParams.get("granular") === "1";
  const ct = (req.headers.get("content-type") || "").toLowerCase();

  if (ct.includes("application/json")) {
    let body: { pdf_sha256?: unknown };
    try {
      body = (await req.clone().json()) as { pdf_sha256?: unknown };
    } catch {
      return proxy(req, env, granular, null);
    }
    const sha = typeof body.pdf_sha256 === "string" ? body.pdf_sha256.trim() : "";
    if (sha) {
      const cached = await tryReplayExtract(env, sha, granular);
      if (cached) return cached;
      return proxy(req, env, granular, sha);
    }
  }
  return proxy(req, env, granular, null);
}

async function tryReplayExtract(env: Env, sha: string, granular: boolean): Promise<Response | null> {
  if (granular) {
    const phase1Key = await cacheKey(sha, {}, "extract-phase-1-min", env.CODEX_VERSION);
    const phase2Key = await cacheKey(sha, {}, "extract", env.CODEX_VERSION);
    const [p1, p2] = await Promise.all([env.CACHE.get(phase1Key), env.CACHE.get(phase2Key)]);
    if (!p1 || !p2) return null;
    // Replay only phase1 + phase2_complete; clients in granular mode
    // tolerate missing intermediate events (they're additive UI
    // refreshes, not required signals).
    const body = buildSseEvent("phase1", p1) + buildSseEvent("phase2_complete", p2);
    return cachedResponse(body);
  }
  const phase1Key = await cacheKey(sha, {}, "extract-phase-1", env.CODEX_VERSION);
  const phase2Key = await cacheKey(sha, {}, "extract", env.CODEX_VERSION);
  const [p1, p2] = await Promise.all([env.CACHE.get(phase1Key), env.CACHE.get(phase2Key)]);
  if (!p1 || !p2) return null;
  const body = buildSseEvent("", p1) + buildSseEvent("", p2);
  return cachedResponse(body);
}

function cachedResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Codex-Edge-Cache": "hit",
    },
  });
}

async function proxy(req: Request, env: Env, granular: boolean, sha: string | null): Promise<Response> {
  const url = new URL(req.url);
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  let forwardBody: BodyInit | null = req.body;
  if (ct.includes("application/json")) {
    forwardBody = await req.text();
  }

  const upstream = await fetch(env.CODEX_ORIGIN_URL + "/v1/extract/stream" + url.search, {
    method: "POST",
    headers: stripHopHeaders(req.headers),
    body: forwardBody,
  });
  if (!upstream.ok || !sha || !upstream.body) {
    return passthrough(upstream);
  }
  const teed = captureExtractStream(upstream.body, env, sha, granular);
  return new Response(teed, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Codex-Edge-Cache": "miss",
    },
  });
}

function captureExtractStream(
  body: ReadableStream<Uint8Array>,
  env: Env,
  sha: string,
  granular: boolean,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let frameIndex = 0; // for non-granular mode: 0=phase1, 1=phase2
  const phase1Ttl = parseInt(env.PHASE1_TTL || "86400", 10);
  const phase2Ttl = parseInt(env.PHASE2_TTL || "604800", 10);

  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { value, done } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(value);
      buf += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, nl);
        buf = buf.slice(nl + 2);
        let event = "";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
        }
        if (dataLines.length === 0) continue;
        const payload = dataLines.join("\n");

        let key: string | null = null;
        let ttl = phase2Ttl;
        if (granular) {
          if (event === "phase1") {
            key = await cacheKey(sha, {}, "extract-phase-1-min", env.CODEX_VERSION);
            ttl = phase1Ttl;
          } else if (event === "phase2_complete") {
            key = await cacheKey(sha, {}, "extract", env.CODEX_VERSION);
            ttl = phase2Ttl;
          }
        } else {
          if (frameIndex === 0) {
            key = await cacheKey(sha, {}, "extract-phase-1", env.CODEX_VERSION);
            ttl = phase1Ttl;
          } else if (frameIndex === 1) {
            key = await cacheKey(sha, {}, "extract", env.CODEX_VERSION);
            ttl = phase2Ttl;
          }
          frameIndex += 1;
        }
        if (key) {
          env.CACHE.put(key, payload, { expirationTtl: ttl }).catch(() => {});
        }
      }
    },
    cancel(reason) {
      reader.cancel(reason).catch(() => {});
    },
  });
}

function stripHopHeaders(headers: Headers): Headers {
  const out = new Headers(headers);
  for (const h of ["host", "content-length", "transfer-encoding", "connection"]) {
    out.delete(h);
  }
  return out;
}

function passthrough(res: Response): Response {
  const out = new Headers(res.headers);
  out.set("X-Codex-Edge-Cache", "bypass");
  return new Response(res.body, { status: res.status, headers: out });
}
