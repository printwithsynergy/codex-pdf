---
title: "Codex change ripple rule"
description: "Every change to codex-pdf must cascade a redeploy of every consumer service in a fixed order."
group: "Operations"
order: 5
slug: "codex-change-ripple"
---

# Codex change ripple rule

Any change to `codex-pdf` — code, env contract, schema, Docker image,
or Railway env var — **MUST** trigger a redeploy of every consumer
service. Skipping the cascade silently pins consumers to a stale
codex contract and is the single most common source of "demo broke
overnight" pages.

## Order

Redeploy in this order so the cache and contract warm sanely:

1. **codex-pdf** — both the shared `codex-pdf-production` service and
   every per-consumer sidecar (`loupe-pdf-marketing/codex-sidecar`,
   `lint-pdf-marketing/codex-sidecar`, `codex-pdf-marketing/codex-sidecar`,
   `lint-pdf-ui/packages/web/codex-sidecar`).
2. **lint-pdf engine + admin** (lint-pdf repo + lint-pdf-ui admin) so
   the rule engine and admin app pick up new client behaviour /
   contract fields before the marketing demos route through them.
3. **All marketing sites**, in any order:
   - `lint-pdf-marketing` (lintpdf.com)
   - `loupe-pdf-marketing` (loupepdf.com)
   - `codex-pdf-marketing` (codexpdf.com)
4. **lint-pdf-ui admin** if it wasn't already redeployed in step 2.

## Trigger

There are three equivalent ways to redeploy:

| How | When |
|---|---|
| `git push origin main` to the consumer repo | When the consumer commit references the new codex version. Railway picks up new commits automatically. |
| Railway dashboard "Redeploy" button | When the consumer repo has no new commits but you need to force a build against the latest base image. |
| GraphQL `serviceInstanceRedeploy` | Scriptable; matches the dashboard "Redeploy" button. Used by the deploy-verify-fix loop in the audit harness. |

## Verification

After each consumer redeploys, run the per-repo smoke script:

- `loupe-pdf-marketing/scripts/smoke-codex-extract.mjs`
- `lint-pdf-marketing/scripts/smoke-codex-extract.mjs`
- `codex-pdf-marketing/scripts/smoke-codex-extract.mjs`

Each script accepts a `CODEX_DEMO_BASE` env var and asserts
`codex_version` matches the deployed codex package version.

For codex itself:

```bash
curl -fsS https://codex-pdf-production.up.railway.app/healthz | jq
# {"status":"ok","version":"1.3.1","ghostscript":true,"cache_backend":"redis"}

curl -fsS https://codex-pdf-production.up.railway.app/v1/contract | jq .package_version
# "1.3.1"
```

In multi-plant mode, also verify contract compatibility and failover:

```bash
curl -fsS https://<plant-codex>/v1/contract | jq .section_schema_versions
curl -fsS https://<shared-codex>/v1/contract | jq .section_schema_versions
```

Both maps must satisfy each consumer's
`CODEX_REQUIRED_SECTION_VERSIONS`. Then force one plant endpoint down
and re-run each smoke script to confirm hybrid failover works.

## Why a fixed order matters

- Marketing sites cache the codex base URL at build time when the
  consumer is server-rendered (Astro). Redeploying marketing first
  bakes in the OLD codex URL, then a fresh codex deploy strands
  marketing on a stale endpoint until the next push.
- lint-pdf and lint-pdf-ui consume codex via subprocess + HTTP. They
  must pick up new client behaviour before the marketing layer
  proxies through them, otherwise "demo extract" succeeds but the
  admin app's codex-cluster CLI shows a different schema.
- Per-deploy sidecars share a Redis cache when configured. Redeploying
  the sidecar with a fresh Docker image ahead of marketing prevents
  cache key drift between an old codex version (pre-redeploy) and a
  new one (post-redeploy) that would otherwise read each other's
  serialized JSON shapes.

## Sidecar mode (Option B)

Each marketing repo ships a sibling `codex-sidecar/` config that
runs the canonical `codex-pdf` Dockerfile alongside the marketing
service. Sidecar redeploys are part of step 1 of the order above.
The shared `codex-pdf-production` service serves admin, lint engine,
and any consumer that hasn't opted into a sidecar.

To switch a marketing service between shared and per-consumer codex,
change one env var on the marketing service:

- Shared: `CODEX_API_BASE_URL=https://codex-pdf-production.up.railway.app`
- Sidecar: `CODEX_API_BASE_URL=https://${{codex-sidecar.RAILWAY_PRIVATE_DOMAIN}}`
  (Railway service-reference; resolves at deploy time)
- Multi-plant hybrid:
  `CODEX_API_BASES=plant-a=https://<codex-a>,shared=https://<codex-shared>`
  + `CODEX_ROUTE_MODE=hybrid` + `CODEX_PLANT=plant-a`

See `codex-pdf/docs/deploy.md` for the full switch-back recipe.
