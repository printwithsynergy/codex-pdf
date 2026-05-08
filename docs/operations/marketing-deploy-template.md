---
title: "Marketing deploy template"
description: "Reusable Railway service layout for any consumer that needs a codex sidecar + optional redis cache + optional clamav."
group: "Operations"
order: 6
slug: "marketing-deploy-template"
---

# Marketing deploy template

Each `*-pdf-marketing` repo declares the same Railway service layout
so a fresh `railway up` in any of them spins up the full stack with
zero manual configuration.

## Services

| Name | Required | Purpose |
|---|---|---|
| `web` (the marketing app) | yes | Astro / Next.js public site + `/api/demo*` proxies |
| `codex` | yes | Per-consumer codex-pdf sidecar (Option B) |
| `redis` | **no** (recommended for prod) | Shared render cache for codex; delete this service for a smaller footprint and codex falls back to the in-memory cache |
| `clamav` | **no** (only where the demo virus-scans) | clamd over TCP for the upload sanitizer |

## Auto-wired env (Railway service references)

Web service:

```
CODEX_API_BASE_URL=https://${{codex.RAILWAY_PRIVATE_DOMAIN}}
NEXT_PUBLIC_CODEX_API_BASE_URL=https://${{codex.RAILWAY_PUBLIC_DOMAIN}}
CLAMAV_HOST=${{clamav.RAILWAY_PRIVATE_DOMAIN}}     # only where used
CLAMAV_PORT=3310
```

Codex service:

```
CODEX_REDIS_URL=${{redis.REDIS_URL}}
CODEX_AUTH_MODE=internal,bearer
CODEX_LOCAL_FALLBACK=1
ALLOW_EXTERNAL_FETCH=true
FETCH_TIMEOUT_MS=15000
FETCH_MAX_BYTES=52428800
```

Service references resolve at deploy time. If the operator deletes
`redis`, `${{redis.REDIS_URL}}` resolves to an empty string and
codex falls back to the in-memory cache automatically (logged
warning, no crash). Same for `clamav`: deleting the service makes
`${{clamav.RAILWAY_PRIVATE_DOMAIN}}` empty, the marketing demo's
`clamAvEnabled()` returns false, and the upload pipeline skips the
virus-scan step with a `"skipped"` status surfaced to the user.

## Manual secrets (Quincy-set, never auto-wired)

| Var | Service | Purpose |
|---|---|---|
| `CODEX_BEARER_TOKEN` | codex + web | Server-to-server bearer auth |
| `CODEX_API_TOKEN` | web | Same value as `CODEX_BEARER_TOKEN`; the marketing site forwards it |
| `CODEX_BASIC_AUTH_USERNAME` / `CODEX_BASIC_AUTH_PASSWORD` | codex | Optional Basic Auth for human curl probes |
| `CODEX_INTERNAL_TOKEN` | codex | Optional internal-token mode for sidecar-only routes |

Generate them with `openssl rand -hex 24` and paste into the Railway
service-vars dashboard. No bearer / Basic-Auth value should ever live
in the marketing browser bundle.

## Healthchecks

| Service | URL |
|---|---|
| Web | `/` |
| Codex | `/healthz` (returns `{status, version, ghostscript, cache_backend}`) |
| Redis | n/a — Railway monitors the redis container directly |
| ClamAV | n/a — clamd is TCP-only |

The web service's `healthcheckPath` and `restartPolicy` are baked
into its `railway.toml`; codex inherits the canonical Dockerfile's
healthcheck.
