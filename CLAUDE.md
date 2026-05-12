# Codex PDF — Agent Guidance

## Service boundary

Codex is the extraction and normalized intelligence layer in the Print with Synergy stack.

- Own extraction, normalization, and reusable summary payloads.
- Keep outputs deterministic, versioned, and backward-compatible.
- Expose detection signals, not policy verdicts.

## Non-goals for this repo

- Do not implement viewer/UI presentation concerns here.
- Do not encode customer policy/rule pass-fail logic here.

Those belong to Loupe (display) and Lint (rules/workflow).

## Offshoot rule

For new products (Forge, Trap, Impose, Marks, etc.), map capabilities to one owner:

1. Display/inspection -> Loupe
2. Rules/reporting/workflow -> Lint
3. Extraction/normalized facts -> Codex

When work spans layers, define a contract seam and keep logic in its owner service.

## Deployed surface (1.8.1)

Codex now runs as **three services** in production. They share the
same content-addressed cache key format
(`codex:{VERSION}:{kind}:{tenant}:{pdf_sha}:{args_sha}`) so a
`VERSION` bump invalidates every tier atomically. ``tenant`` is
``"default"`` for single-tenant deployments; multi-tenant
deployments route on the ``X-Codex-Tenant`` request header.

### 1. codex-pdf API (Railway, project `lintpdf.com`)

- Service: `codex-pdf-lint-sidecar`
  (id `1fb13ff5-0c5c-4c8f-90dc-0fd5b447a937`).
- URL: `https://codex-pdf-lint-sidecar-production.up.railway.app`.
- Source: `printwithsynergy/codex-pdf` `main`, root `Dockerfile`,
  `numReplicas = 1` (single replica is enough for the lintpdf.com
  demo traffic profile; scale via Railway GraphQL `serviceInstanceUpdate`
  when traffic warrants).
- Auth: bearer (`CODEX_BEARER_TOKEN`) or internal (`CODEX_INTERNAL_TOKEN`).
- Backing Redis: `redis://default:…@redis.railway.internal:6379`
  (set on the service as `CODEX_REDIS_URL`).

### 2. codex-speculator (Railway sidecar, same project)

- Service: `codex-speculator`
  (id `ebe2fa94-6003-4ee4-a8af-df53d6e0892c`), `numReplicas = 1`.
- Same Docker image as the API; uses `railway.speculator.toml` so the
  start command becomes `python -m codex_pdf.speculator`.
- Subscribes to Redis Stream `codex:speculate` (XADD'd from
  `/v1/probe` and `RedisBlobStore.put`) and pre-runs Phase 1 +
  Phase 2 so `/v1/extract` lands warm. Idempotent: cache-hit
  short-circuit collapses replays to a single Redis GET.
- Stream is auto-trimmed to `MAXLEN ~10000`. Speculator failures are
  invisible to the API — origin behaviour does not depend on it.

### 3. codex-edge (Cloudflare Worker + KV)

- URL: <https://codex-edge.thinkneverland.workers.dev>.
- Account: `99aa3f9229469650a746a7d39ac58448`
  (`Quincy@thinkneverland.com's Account`).
- KV namespace `CACHE`: id `89a21ce1937046018a3d9d38f4e763ff`
  (preview `a4856d6f3b244087b907c189c2a2277d`).
- Origin (`CODEX_ORIGIN_URL` var) points at the Railway API.
- Caches: `probe-min`, `probe-std`, `extract-phase-1`,
  `extract-phase-1-min`, `extract`. Hash-keyed JSON requests hit
  edge; multipart uploads bypass to origin.
- `ctx.waitUntil` keeps the Worker alive long enough to persist
  every SSE frame to KV before the response stream closes.

### 4. Retention surface (R2)

Opt-in PDF persistence for the marketing demo. Triggered only by
an explicit `retain_for_training=true` form field or
`X-Codex-Retain-For-Training: true` header on `POST /v1/extract`;
no flag = no write.

- R2 bucket: `codex-retain` (same Cloudflare account as the edge
  Worker). 90-day expiry lifecycle rule applied at the bucket
  level — the app does not manage retention timing.
- S3 endpoint: `https://99aa3f9229469650a746a7d39ac58448.r2.cloudflarestorage.com`.
- Region: `auto` (R2 requirement).
- Token: account-scoped Cloudflare API token, R2 Storage Bucket
  Item Write, bucket-scoped to `codex-retain`. Access key id =
  the token's `id`; secret access key = `sha256(token.value)`.
- Service env on `codex-pdf-lint-sidecar`: `CODEX_RETAIN_BUCKET`,
  `CODEX_RETAIN_PREFIX` (`codex/prod`), `CODEX_RETAIN_TTL_DAYS`
  (`90`, informational only), `CODEX_RETAIN_ENDPOINT_URL`,
  `CODEX_RETAIN_REGION`, `CODEX_RETAIN_ACCESS_KEY_ID`,
  `CODEX_RETAIN_SECRET_ACCESS_KEY`. Unset
  `CODEX_RETAIN_BUCKET` to disable persistence (the retain branch
  becomes a no-op).
- Object key layout:
  `codex/prod/tenant={tenant}/dt={YYYY-MM-DD}/sha256={hex64}/{document.pdf,extract.json,meta.json}`.
- Delete endpoint: `POST /v1/retention/delete` with
  `{"sha256": "…"}` removes all three objects for that sha across
  every date partition under the configured tenant prefix.

### Bumping VERSION

When `codex_pdf.version.VERSION` changes:

1. Tag + publish the Python package and TS client.
2. Update `codex-edge/wrangler.toml`'s `CODEX_VERSION` var to match.
3. Re-deploy the Worker (`wrangler deploy`). Cache keys rotate
   automatically — no KV purge needed.
4. Railway autodeploys the API + speculator from `main`.
