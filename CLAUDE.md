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

## Deployed surface (1.7.2)

Codex now runs as **three services** in production. They share the
same content-addressed cache key format
(`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`) so a `VERSION` bump
invalidates every tier atomically.

### 1. codex-pdf API (Railway, project `lintpdf.com`)

- Service: `codex-pdf-lint-sidecar`
  (id `1fb13ff5-0c5c-4c8f-90dc-0fd5b447a937`).
- URL: `https://codex-pdf-lint-sidecar-production.up.railway.app`.
- Source: `printwithsynergy/codex-pdf` `main`, root `Dockerfile`,
  `numReplicas = 3`.
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

### Bumping VERSION

When `codex_pdf.version.VERSION` changes:

1. Tag + publish the Python package and TS client.
2. Update `codex-edge/wrangler.toml`'s `CODEX_VERSION` var to match.
3. Re-deploy the Worker (`wrangler deploy`). Cache keys rotate
   automatically — no KV purge needed.
4. Railway autodeploys the API + speculator from `main`.
