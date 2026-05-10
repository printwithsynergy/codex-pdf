---
title: "Marketing deploy template"
description: "Reusable Railway service layout for any consumer marketing site that calls codex-pdf."
group: "Operations"
order: 6
slug: "marketing-deploy-template"
---

# Marketing deploy template

Each `*-pdf-marketing` repo declares the same Railway service layout
so a fresh `railway up` in any of them spins up the full stack with
zero manual configuration.

The deployed codex itself runs as a single shared instance —
[`CLAUDE.md`](../../CLAUDE.md) has the URLs / IDs. Marketing sites
point at the shared codex (or its Cloudflare-edge alias) rather
than running their own sidecar.

## Services

| Name | Required | Purpose |
|---|---|---|
| `web` (the marketing app) | yes | Astro / Next.js public site + `/api/demo*` proxies |
| `redis` | **no** (recommended for prod) | Optional same-region cache the marketing site reads / writes |
| `clamav` | **no** (only where the demo virus-scans) | clamd over TCP for the upload sanitizer |

## Auto-wired env (Railway service references)

Web service:

```
CODEX_API_BASE_URL=https://codex-edge.thinkneverland.workers.dev
NEXT_PUBLIC_CODEX_API_BASE_URL=https://codex-edge.thinkneverland.workers.dev
CLAMAV_HOST=${{clamav.RAILWAY_PRIVATE_DOMAIN}}     # only where used
CLAMAV_PORT=3310
```

The Cloudflare Worker (`codex-edge`) is the recommended public
entry point: hash-keyed JSON requests hit the global KV cache,
multipart uploads transparently bypass to the Railway origin. The
Worker URL is stable across `VERSION` bumps.

If you need to bypass the edge (for example, to test a brand-new
contract field before the Worker's `CODEX_VERSION` ticks), point
straight at the Railway origin instead:

```
CODEX_API_BASE_URL=https://codex-pdf-lint-sidecar-production.up.railway.app
```

## Manual secrets (operator-set, never auto-wired)

| Var | Service | Purpose |
|---|---|---|
| `CODEX_BEARER_TOKEN` | web | Server-to-server bearer auth — must match the value set on the codex API service. |
| `CODEX_API_TOKEN` | web | Same value as `CODEX_BEARER_TOKEN`; the marketing site forwards it as the `Authorization` header. |

Generate them with `openssl rand -hex 24` and paste into the Railway
service-vars dashboard. **No bearer value should ever live in the
marketing browser bundle** — the marketing app proxies authenticated
codex calls through its own server-side `/api/demo*` routes.

## Healthchecks

| Service | URL |
|---|---|
| Web | `/` |
| Redis | n/a — Railway monitors the redis container directly |
| ClamAV | n/a — clamd is TCP-only |

Codex itself is monitored separately; its `/v1/healthz` is in the
codex-pdf repo's `Dockerfile` and the Cloudflare Worker has its
own `/edge/healthz` that proxies the origin status.
