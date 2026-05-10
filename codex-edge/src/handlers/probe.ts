/**
 * `POST /v1/probe` handler.
 *
 * KV cache hit → replay both events (probe-min + probe-std) from KV
 * in <15 ms. KV miss → tee the upstream SSE stream into KV while
 * relaying bytes to the client so cold latency = origin latency, no
 * extra round-trip.
 *
 * Auth and content-type are forwarded untouched. Multipart uploads
 * always proxy to origin (the bytes haven't been seen before, so
 * caching is impossible until origin returns the sha — which the
 * tee-and-cache path handles automatically).
 */

import type { Env } from "../env";
import { cacheKey } from "../cache_key";
import { buildSseEvent } from "../sse_tee";

export async function handleProbe(
  req: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  // Hash-keyed JSON body lets us check KV before talking to origin.
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("application/json")) {
    let body: { pdf_sha256?: unknown };
    try {
      body = (await req.clone().json()) as { pdf_sha256?: unknown };
    } catch {
      return proxy(req, env, ctx);
    }
    const sha = typeof body.pdf_sha256 === "string" ? body.pdf_sha256.trim() : "";
    if (sha) {
      const cached = await tryReplayProbe(env, sha);
      if (cached) return cached;
    }
  }
  return proxy(req, env, ctx);
}

async function tryReplayProbe(env: Env, sha: string): Promise<Response | null> {
  const minKey = await cacheKey(sha, {}, "probe-min", env.CODEX_VERSION);
  const stdKey = await cacheKey(sha, {}, "probe-std", env.CODEX_VERSION);
  const [minVal, stdVal] = await Promise.all([env.CACHE.get(minKey), env.CACHE.get(stdKey)]);
  if (!minVal || !stdVal) return null;
  const body =
    buildSseEvent("", minVal) + buildSseEvent("", stdVal);
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Codex-Edge-Cache": "hit",
    },
  });
}

async function proxy(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  // We need the sha to write to KV after the stream completes. For
  // the JSON-body path we already saw it; for multipart we'd have to
  // hash the upload, which is expensive — for now, multipart bypasses
  // edge caching and goes straight to origin.
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  let sha: string | null = null;
  let forwardBody: BodyInit | null = req.body;
  if (ct.includes("application/json")) {
    const text = await req.text();
    try {
      const parsed = JSON.parse(text) as { pdf_sha256?: unknown };
      if (typeof parsed.pdf_sha256 === "string") sha = parsed.pdf_sha256.trim();
    } catch {
      // pass-through; origin will reject malformed JSON
    }
    forwardBody = text;
  }

  const upstream = await fetch(env.CODEX_ORIGIN_URL + "/v1/probe", {
    method: "POST",
    headers: stripHopHeaders(req.headers),
    body: forwardBody,
  });
  if (!upstream.ok || !sha || !upstream.body) {
    return passthrough(upstream);
  }

  const minKey = await cacheKey(sha, {}, "probe-min", env.CODEX_VERSION);
  const stdKey = await cacheKey(sha, {}, "probe-std", env.CODEX_VERSION);
  return new Response(orderedProbeCache(upstream.body, env, ctx, minKey, stdKey), {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Codex-Edge-Cache": "miss",
    },
  });
}

function orderedProbeCache(
  body: ReadableStream<Uint8Array>,
  env: Env,
  ctx: ExecutionContext,
  minKey: string,
  stdKey: string,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let frameIndex = 0;
  const probeTtl = parseInt(env.PROBE_TTL || "86400", 10);

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
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
        }
        if (dataLines.length === 0) continue;
        const payload = dataLines.join("\n");
        const target = frameIndex === 0 ? minKey : frameIndex === 1 ? stdKey : null;
        if (target) {
          // ctx.waitUntil keeps the Worker alive long enough for the
          // KV write to land — without this, the Worker is killed as
          // soon as the response stream closes and the second event's
          // put silently aborts.
          ctx.waitUntil(
            env.CACHE.put(target, payload, { expirationTtl: probeTtl }).catch(() => {}),
          );
        }
        frameIndex += 1;
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
