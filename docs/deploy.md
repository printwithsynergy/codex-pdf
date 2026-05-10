---
title: "Deploy"
description: "Run codex-pdf as a three-service surface: Railway API + speculator sidecar + Cloudflare edge cache."
group: "Operations"
order: 4
slug: "deploy"
---

# Deploying codex-pdf

`codex-pdf 1.7.0` ships a single Dockerfile + a Cloudflare Worker.
In production the same image runs as **two Railway services**, plus
a third tier on Cloudflare:

1. **codex-pdf API** — FastAPI under gunicorn + uvicorn workers.
   The HTTP contract.
2. **codex-speculator** — same image, started with
   `CODEX_MODE=speculator`. Subscribes to a Redis Stream and
   pre-warms Phase 1 + Phase 2 caches.
3. **codex-edge** — Cloudflare Worker (in [`codex-edge/`](../codex-edge))
   that fronts the API with a KV-backed write-through cache for
   probe + extract.

All three share the same content-addressed cache key namespace
(`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`), so a `VERSION`
bump invalidates every tier atomically. Deployed URLs and account
IDs live in [`CLAUDE.md`](../CLAUDE.md); this doc is for setting up
your own copy.

## Image

[`Dockerfile`](../Dockerfile):

- Base: `python:3.12-slim`.
- System packages: `ghostscript >= 10`, `poppler-utils`, `libheif1`,
  `tini`, `ca-certificates`, `curl`.
- App layer: `uv sync --frozen --no-dev` against the repo's
  `pyproject.toml` + `uv.lock`.
- Runs as the non-root `codex` user.
- Healthcheck hits `/v1/healthz`.
- `ENTRYPOINT tini --` so reaped child processes don't leak.
- `CMD` dispatches on `$CODEX_MODE`:
  - unset / `api` → gunicorn + uvicorn workers serving FastAPI
    (`CODEX_WORKERS` defaults to 2).
  - `speculator` → `python -m codex_pdf.speculator`.

Build locally:

```bash
docker build -t codex-pdf:1.7.0 .
docker run --rm -p 8080:8080 codex-pdf:1.7.0
curl localhost:8080/v1/healthz
```

## Auth

Set one or more of the following so the API rejects unauthenticated
requests:

| Env | Header presented | Mode |
|---|---|---|
| `CODEX_BEARER_TOKEN` | `Authorization: Bearer …` | bearer |
| `CODEX_API_KEY` | `X-Codex-Key: …` | api-key |
| `CODEX_INTERNAL_TOKEN` | `X-Codex-Internal: …` | internal (sidecar) |

`CODEX_AUTH_MODE` accepts a comma-separated list to lock the
surface, e.g. `CODEX_AUTH_MODE=bearer,internal`. The default is
"auto" — every mode whose token is configured.

## Cache + blob store

Default is in-process LRU. Set `CODEX_REDIS_URL=redis://…` to share
a cache across replicas. The image's full venv already contains the
`redis` Python client.

`RedisBlobStore` shares the same Redis to hold inbound PDF bytes
keyed by `pdf_sha256`, so subsequent hash-keyed calls (`POST
/v1/probe` / `POST /v1/extract` with `{"pdf_sha256": "…"}`) skip
re-uploading the file. The blob store TTL defaults to 1 h.

## Railway: codex-pdf API

The canonical config is [`railway.toml`](../railway.toml). Service
variables, at minimum:

- `CODEX_BEARER_TOKEN` (or `CODEX_INTERNAL_TOKEN` for sidecar use)
- `CODEX_REDIS_URL` (Railway-provisioned Redis)
- `CODEX_AUTH_MODE` if you want to lock to a specific mode

Set `numReplicas` per the load profile; the production deployment
runs three replicas behind Railway's load balancer.

## Railway: codex-speculator sidecar

Same image, same repo, same Dockerfile. The sidecar service only
needs:

- `CODEX_MODE=speculator`
- `CODEX_REDIS_URL` (must be the same Redis the API points at)
- `numReplicas = 1`

The speculator XADD-trims the `codex:speculate` stream
automatically (`MAXLEN ~ 10000`). Speculator failures are invisible
to the API — origin behaviour does not depend on it; it only
warms caches.

`POST /v1/probe` and `RedisBlobStore.put` both XADD onto the
stream, so any sha that lands on the API gets pre-warmed by the
speculator before the next request.

## Cloudflare: codex-edge

[`codex-edge/`](../codex-edge) is a Cloudflare Worker + KV
namespace.

```bash
cd codex-edge
wrangler kv namespace create CACHE
wrangler deploy
```

`wrangler.toml` defines:

- `CODEX_ORIGIN_URL` — the Railway API base URL.
- `CODEX_VERSION` — must match `codex_pdf.version.VERSION` so KV
  keys line up.
- `PROBE_TTL`, `PHASE1_TTL`, `PHASE2_TTL` — cache TTLs (seconds).

Hash-keyed JSON requests hit edge KV first; multipart uploads
bypass to origin. `ctx.waitUntil` keeps the Worker alive long
enough for every SSE frame to land in KV before the response
stream closes.

## Local development without HTTP

Set `CODEX_API_BASE` empty (the default) and
`codex_pdf.client.HttpClient` falls back to in-process calls. This
is the path the codex CLI uses during tests; it does **not** spin
up a server.

For an actual local HTTP server, use the CLI's `serve` subcommand:

```bash
uv run codex-pdf serve --host 0.0.0.0 --port 8080
```

That runs the same FastAPI app under uvicorn, single-process — fine
for development. The Dockerfile path uses gunicorn + uvicorn
workers for production concurrency.

## Bumping VERSION

When `codex_pdf.version.VERSION` changes:

1. Tag + publish the Python package (`uv build && uv publish`)
   and TS client (`npm publish` from `clients/ts`).
2. Update `codex-edge/wrangler.toml`'s `CODEX_VERSION` var to
   match.
3. Re-deploy the Worker (`wrangler deploy`). Cache keys rotate
   automatically — no KV purge needed.
4. Railway autodeploys the API + speculator from `main`.

The version-bump checklist also lives in
[`CONTRIBUTING.md`](../CONTRIBUTING.md).
