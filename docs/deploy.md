---
title: "Deploy"
description: "Run codex-pdf as a Railway service (shared and per-consumer sidecars), or any container host."
group: "Operations"
order: 4
slug: "deploy"
---

# Deploying codex-pdf

`codex-pdf` 1.2.0 ships a Dockerfile + Railway config so the same
image runs in three modes:

1. **Shared service** — one codex deployment serves every consumer
   (`lint-pdf` admin, `loupe-pdf` viewer host, `codex-pdf-marketing`).
2. **Per-consumer sidecar (Option B)** — a private codex deployment
   runs alongside each marketing site so the public surface only ever
   talks to its sibling codex.
3. **Hybrid** — admin uses the shared service; marketing sites pin to
   sidecars for stable demo behaviour.

## Image

`codex-pdf/Dockerfile`:

- Base: `python:3.12-slim`.
- System packages: `ghostscript >= 10`, `poppler-utils`, `libheif1`,
  `tini`, `ca-certificates`, `curl`.
- App layer: `uv sync --frozen --no-dev` against the repo's
  `pyproject.toml` + `uv.lock`.
- Runs as the non-root `codex` user.
- Healthcheck hits `/v1/healthz`.
- `ENTRYPOINT tini --` so reaped child processes don't leak.

Build locally:

```bash
docker build -t codex-pdf:1.2.0 codex-pdf
docker run --rm -p 8080:8080 codex-pdf:1.2.0
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

`CODEX_AUTH_MODE` accepts a comma-separated list to lock the surface,
e.g. `CODEX_AUTH_MODE=bearer,internal`. Default is "auto" — every mode
whose token is configured.

## Cache

Default is in-process LRU. Set `CODEX_REDIS_URL=redis://…` to share a
cache across replicas (requires `redis` python package — installed
when codex-pdf is run from the Docker image's full venv).

## Railway

The canonical config is `codex-pdf/railway.toml`. Per-consumer
sidecars:

- `loupe-pdf-marketing/codex-sidecar/railway.toml`
- `lint-pdf-ui/packages/web/codex-sidecar/railway.toml`

Both sidecars build the canonical `codex-pdf/Dockerfile`. In Railway
the operator sets:

- `rootDirectory = codex-pdf` (or the sidecar dir, with build context
  overridden to `../../codex-pdf`)
- `dockerfilePath = Dockerfile`
- Service variables — at minimum `CODEX_INTERNAL_TOKEN` and
  `CODEX_AUTH_MODE = internal`.

Marketing sites then point at the sibling codex with:

- `CODEX_API_BASE = https://<codex-sidecar-private-host>` (server side)
- `NEXT_PUBLIC_CODEX_API_BASE_URL = …` (browser side, when applicable)
- `CODEX_API_TOKEN = <CODEX_INTERNAL_TOKEN>` (server side)
- `CODEX_TIMEOUT_MS = 60000`

The shared codex consumed by `lint-pdf` admin / `lint-pdf-ui`
packages/app / `codex-pdf-marketing` should run with bearer auth and
its `CODEX_BEARER_TOKEN` rotated through the operator's secret store.

## Switching between shared and per-consumer (Option B)

The same image, lockfile, and `Procfile` run in either mode —
switching is a service-vars + DNS change, not a code change.

### Shared → per-consumer (lint marketing example)

1. New Railway service in the lintpdf.com project pointing at the
   `codex-pdf` repo.
2. Set "Root Directory" to `codex-pdf` so the canonical Dockerfile
   is the build context (or, if you keep the sidecar config in the
   marketing repo, override it via Railway's "Build Context" field
   — see `lint-pdf-ui/packages/web/codex-sidecar/railway.toml`).
3. Provision env vars (`CODEX_BEARER_TOKEN`, `CODEX_INTERNAL_TOKEN`,
   etc.) — `openssl rand -hex 24` for both.
4. Update the marketing service:
   ```
   CODEX_API_BASE_URL=<new sidecar URL>
   CODEX_API_TOKEN=<sidecar bearer>
   ```
5. Trigger a marketing redeploy so it picks up the new base URL.

### Per-consumer → shared

1. Reset the marketing service vars to the shared codex:
   ```
   CODEX_API_BASE_URL=https://codex-pdf-production.up.railway.app
   CODEX_API_TOKEN=<shared production bearer>
   ```
2. Pause / archive the sidecar service in the Railway dashboard.

Both modes preserve `/healthz` (un-auth) and `/extract` aliases so
ops probes / curl flows from either side keep working unchanged.

## Local development without HTTP

Set `CODEX_API_BASE` empty (the default) and the
:class:`codex_pdf.client.HttpClient` falls back to in-process calls.
This is the path lint-pdf and the codex CLI use during tests; it does
**not** spin up a server.
