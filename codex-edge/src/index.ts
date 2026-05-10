/**
 * codex-edge: Cloudflare Worker entrypoint.
 *
 * Routes:
 *   POST /v1/probe              → KV write-through SSE proxy
 *   POST /v1/extract/stream     → KV write-through SSE proxy
 *   GET  /edge/healthz          → "ok" + ms-to-origin
 *   *                           → forwarded to origin (no caching)
 *
 * Caching rules (also documented in /CLAUDE.md and the plan file):
 *   - Hash-keyed JSON requests can hit cache; multipart uploads
 *     bypass and go to origin.
 *   - KV keys mirror the Python `cache_key()` exactly so VERSION
 *     bumps invalidate origin Redis and edge KV in lockstep.
 *   - Auth headers are forwarded; the Worker never inspects the
 *     PDF payload itself.
 */

import type { Env } from "./env";
import { handleProbe } from "./handlers/probe";
import { handleExtractStream } from "./handlers/extract";

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/edge/healthz") {
      return health(env);
    }

    if (req.method === "POST") {
      if (url.pathname === "/v1/probe") return handleProbe(req, env);
      if (url.pathname === "/v1/extract/stream") return handleExtractStream(req, env);
    }

    // Anything else just proxies to origin so the edge worker is a
    // drop-in replacement DNS-wise.
    return forwardToOrigin(req, env);
  },
};

async function forwardToOrigin(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const target = env.CODEX_ORIGIN_URL.replace(/\/$/, "") + url.pathname + url.search;
  const headers = new Headers(req.headers);
  for (const h of ["host", "content-length", "transfer-encoding", "connection"]) {
    headers.delete(h);
  }
  return fetch(target, {
    method: req.method,
    headers,
    body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
    redirect: "manual",
  });
}

async function health(env: Env): Promise<Response> {
  const start = Date.now();
  let originStatus = 0;
  try {
    const res = await fetch(env.CODEX_ORIGIN_URL + "/v1/healthz", { method: "GET" });
    originStatus = res.status;
  } catch {
    originStatus = -1;
  }
  return new Response(
    JSON.stringify({
      status: "ok",
      origin_status: originStatus,
      origin_rtt_ms: Date.now() - start,
      codex_version: env.CODEX_VERSION,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}
