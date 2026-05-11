---
title: "Codex change ripple rule"
description: "Every change to codex-pdf must cascade a redeploy of every consumer service in a fixed order."
group: "Operations"
order: 5
slug: "codex-change-ripple"
---

# Codex change ripple rule

Any change to `codex-pdf` — code, schema, Docker image, env contract,
or `codex_pdf.version.VERSION` — **MUST** cascade a redeploy of every
consumer that calls codex. Skipping the cascade silently pins
consumers to a stale contract.

Cache keys (`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`) rotate
atomically across all three codex tiers when `VERSION` changes — but
consumer apps still cache the codex base URL / contract assumptions
at build time, so a bare codex redeploy alone is not enough.

## Order

1. **codex-pdf** — the three deployed services rotate together:
   - codex-pdf API (Railway, auto-deploys on `main` push)
   - codex-speculator (Railway sidecar, same image; redeploy after
     the API)
   - codex-edge (Cloudflare Worker — bump `CODEX_VERSION` in
     `codex-edge/wrangler.toml` and `wrangler deploy`)
   - See [`CLAUDE.md`](../../CLAUDE.md) for service IDs and the
     full version-bump checklist.
2. **lint-pdf engine + admin** (`lint-pdf` repo + `lint-pdf-ui`
   admin) so the rule engine and admin app pick up new client
   behaviour and contract fields before the marketing demos route
   through them.
3. **All marketing sites**, in any order:
   - `lint-pdf-marketing` (lintpdf.com)
   - `loupe-pdf-marketing` (loupepdf.com)
   - `codex-pdf-marketing` (codexpdf.com)

## Trigger

There are three equivalent ways to redeploy:

| How | When |
|---|---|
| `git push origin main` to the consumer repo | When the consumer commit references the new codex version. Railway picks up new commits automatically. |
| Railway dashboard "Redeploy" button | When the consumer repo has no new commits but you need to force a build against the latest base image. |
| GraphQL `serviceInstanceRedeploy` | Scriptable; matches the dashboard "Redeploy" button. |

## Verification

After codex itself redeploys:

```bash
curl -fsS https://codex-pdf-lint-sidecar-production.up.railway.app/v1/version | jq
# {"version":"1.8.1"}

curl -fsS https://codex-edge.thinkneverland.workers.dev/edge/healthz | jq
# {"status":"ok","origin_status":200,"origin_rtt_ms":...,"codex_version":"1.8.1"}
```

After each consumer redeploys, run its smoke script:

- `loupe-pdf-marketing/scripts/smoke-codex-extract.mjs`
- `lint-pdf-marketing/scripts/smoke-codex-extract.mjs`
- `codex-pdf-marketing/scripts/smoke-codex-extract.mjs`

Each script accepts a `CODEX_DEMO_BASE` env var and asserts
`codex_version` matches the deployed codex package version.

## Why a fixed order matters

- Marketing sites (Astro / Next.js) cache the codex base URL at
  build time. Redeploying marketing first against a stale codex
  bakes in the OLD URL; a fresh codex deploy then strands marketing
  on a stale endpoint until the next push.
- `lint-pdf` and `lint-pdf-ui` consume codex via subprocess + HTTP.
  They must pick up new client behaviour before the marketing layer
  proxies through them, otherwise "demo extract" succeeds but the
  admin app's codex-cluster CLI shows a different schema.
- The codex-edge Worker keys its KV cache by `CODEX_VERSION`.
  Redeploying the Worker last (after the API + speculator) means
  the new key namespace is pristine; the OLD namespace ages out via
  TTL with no further reads.
