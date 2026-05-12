# Codex policies — long-tail SLAs

Policy SLAs that govern the unified extraction surface. These are
**part of the public contract** — consumers can depend on them
across releases. Changes to a policy here are versioned alongside
the schema.

## ConformanceProfile enum

`ConformanceProfile` (the `{profile}` path component on
`POST /v1/documents/{document_id}/conformance/{profile}`) is
**forward-compatible**. The wire shape is a plain string; the
server validates against an allowlist (rejects unknown values with
`400`), but consumers reading verdicts must treat profile keys as
opaque.

### Versioning rule

Adding a new profile is **additive** (minor bump on the codex
package; no schema_version change). The CodexDocument's
`conformance_verdicts` map is `dict[str, CodexConformanceVerdict]`
on the wire; a new key arriving from a newer server must not
break an older consumer.

Removing or renaming a profile is **breaking** (major bump). We
have committed to not do this within the 1.x line.

### Consumer SLA

- Treat the profile string as opaque. Don't enumerate against a
  hard-coded list when iterating `conformance_verdicts.keys()`.
- If you need to gate UI behaviour on a specific profile, match
  against a literal string and ignore others.
- A `400 Bad Request` on `POST .../conformance/{profile}` means
  the server doesn't know that profile yet. The current allowlist
  is published in `GET /v1/contract`.

### Initial profile set (1.9.x)

`pdfx4`, `pdfx1a`, `pdfx3`, `pdfa1b`, `pdfa2b`, `pdfa3b`, `pdfua1`.

Future candidates (not yet implemented): `pdfx6`, `pdfa4`,
`pdfua2`. These will land additively in 1.x.

## Cache TTL

All cache backends honour a single TTL knob:

| Env var | Default | Applies to |
| --- | --- | --- |
| `CODEX_CACHE_TTL_SECONDS` | `86400` (24 h) | `RedisCache` SETEX |
| `CODEX_PDF_BLOB_TTL_SECONDS` | `3600` (1 h) | Blob store SETEX |
| (n/a) | LRU only | `MemoryCache` |

`MemoryCache` is LRU-only by design — process memory is bounded
by total bytes, not time. Operators expecting time-based eviction
should run with `CODEX_REDIS_URL` set; otherwise entries live
until evicted by size pressure (`DEFAULT_BLOB_MAX_BYTES = 500 MB`
on blob; 256-entry LRU on cache).

### Eviction guarantees

- A successful write is durable for at least the TTL window on
  Redis (modulo Redis memory pressure / eviction policy
  configured externally). Operators choosing a smaller maxmemory
  + `allkeys-lru` policy override this — that's an operator
  decision and visible in Redis metrics, not codex's.
- A cache miss is always recoverable: every cached artifact is
  derivable from the source PDF + the cache key arguments.

## Backpressure

The codex API has two layers of load shedding:

1. **Token-bucket rate limit** (Phase 2). Per `(tenant, endpoint)`
   bucket; bucket exhausted → `429 Too Many Requests` +
   `Retry-After` header. Knobs: `CODEX_RATE_LIMIT_RPM` (default
   120), `CODEX_RATE_LIMIT_BURST` (default 30),
   `CODEX_RATE_LIMIT_DISABLED` (off-switch).
2. **Executor pool saturation**. PyMuPDF + pikepdf passes share
   `_EXTRACT_POOL`. When the pool's queue is full, requests
   queue at the executor (FastAPI level). There is no explicit
   `503` from codex today; long queue depth manifests as request
   timeouts on the client side.

### Backpressure SLA

- `429` is the only shed-response codex emits as a deliberate
  signal. Consumers MUST honour `Retry-After`.
- `503` from codex indicates a hard failure (e.g. cache backend
  unreachable, GhostScript missing); it's not used as a
  backpressure code today.
- Distributed (Redis-backed) rate-limit counters are on the
  roadmap. The in-process limiter today is per-replica; effective
  fleet limit is `N × rpm`. This is acceptable for the rc.x
  series and the immediate post-1.9.0 window.

## Observability

Prometheus surface on `/metrics`:

| Metric | Type | Labels |
| --- | --- | --- |
| `codex_api_requests_total` | Counter | `endpoint`, `status` |
| `codex_api_request_seconds` | Histogram | `endpoint` |
| `codex_api_cache_lookups_total` | Counter | `endpoint`, `outcome` |
| `codex_api_stage_seconds` | Histogram | `stage` |

The stage histogram observes the same numbers consumers see in
the response envelope's `stage_durations_ms` — Grafana panels can
correlate without re-deriving.

See [`slos.md`](./slos.md) for the published latency / availability
targets and recommended alert thresholds.

## AI signals (1.3.0)

Detection signals derived from AI models (vision, language, document
classification) live in codex's data layer. They're opt-in for both
the operator and the caller — and codex always emits a structured
warning when the signals come back empty so consumers know why.

### Operator switch

| Env | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_ENABLED` | `false` | When `true`, codex's AI extractors (language, logos, symbols, classification, spell, OCR) run on every extract. When `false`, all AI signal fields stay empty and codex emits `CodexWarning(code="ai_disabled", scope="signals.ai")` in `extraction_warnings`. |

### Caller switch

`X-Codex-Skip-AI: true` request header opts the caller out of AI
extraction even when the operator has it on. Same warning shape
(`code="ai_skipped"`) so consumers can render an honest "AI signals
not run for this request" state.

### Per-tenant entitlements (1.14.0 +)

Operators can pilot AI on a subset of tenants before rolling it out
fleet-wide:

| Env | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_TENANTS_ALLOWLIST` | unset | Comma-separated tenant slugs. When set, ONLY these tenants run AI; everyone else gets `ai_tenant_excluded`. |
| `CODEX_AI_TENANTS_DENYLIST` | unset | Comma-separated tenant slugs blocked from AI. Allowlist wins when both are set. |

### Warning catalogue

| `code` | `scope` | Meaning |
| --- | --- | --- |
| `ai_disabled` | `signals.ai` | Operator gate is off. Affects every request. |
| `ai_skipped` | `signals.ai` | Caller opted out for this request. |
| `ai_tenant_excluded` | `signals.ai` | Operator opted in but the requesting tenant is gated out by `CODEX_AI_TENANTS_ALLOWLIST` / `DENYLIST` (1.14.0 +). |
| `ai_missing_credentials` | `signals.ai` | Operator opted in but the `anthropic` SDK isn't importable or `ANTHROPIC_API_KEY` is unset. Install `codex-pdf[ai]` and set the key to populate signal fields. |
| `ai_tier` | `signals.ai` | Informational. Emitted on every extract when AI ran; the warning's `message` carries `"cpu+claude"` (Tier 1) or `"gpu"` (Tier 2) plus the realised dollar spend, so consumers know which backend produced the signals and what it cost. |
| `ai_budget_exceeded` | `signals.<kind>` | The per-request cost cap (`CODEX_AI_COST_CAP_USD_PER_REQUEST`) was hit mid-extract; signal fields for the affected kinds are empty. Combines additively with `ai_tier`. |

Exactly one of `ai_disabled` / `ai_skipped` / `ai_tenant_excluded` /
`ai_missing_credentials` / `ai_tier` always lands on every
`/v1/extract` response. Consumers MUST NOT branch on the absence
of these warnings — branch on the presence of the specific code
instead.

### Cache key contract

Per-resource endpoint: `GET /v1/documents/{pdf_hash}/signals/{kind}`.

| kind | cache key |
| --- | --- |
| `language` | `(tenant, pdf_hash, page_index, "language")` |
| `logos` | `(tenant, pdf_hash, page_index, "logos")` |
| `symbols` | `(tenant, pdf_hash, page_index, "symbols")` |
| `barcodes` | `(tenant, pdf_hash, page_index, "barcodes")` |
| `spell` | `(tenant, pdf_hash, page_index, "spell")` |
| `classification` | `(tenant, pdf_hash, "classification")` |

Stable across versions. Idempotent: same key → same bytes.

### Forward compatibility

The `SignalKind` enum is intentionally extensible. Future codex
releases may add `images`, `fonts`, `dieline_detected`, etc.
Consumers reading signals MUST treat unknown `kind` strings as
opaque so older clients don't break against newer servers.

### Two AI backends — CPU+Claude vs optional GPU

Most self-hosters and the public demo will never have access to a
GPU. The AI signal extractors are designed around two backends; the
default is **CPU+Claude only** so deployments don't accidentally
spin up GPU bills.

| Tier | When | Backend | Cost shape |
| --- | --- | --- | --- |
| **1 — Default / Demo / OSS** | unset / `CODEX_AI_GPU_URL=""` | Claude Haiku 4.5 for text + vision; CPU libs (`pyzbar`, `pylibdmtx`, perceptual hashing) for specialised tasks | Per-call, scales with traffic. Aggressive 1h prompt cache + content-addressed `(tenant, pdf_hash, kind)` cache eliminates repeat cost. |
| **2 — SaaS / Enterprise (optional)** | `CODEX_AI_GPU_URL=https://...` | Self-hosted GPU service (Modal / RunPod / on-prem) for embedding-heavy workloads (font similarity, visual diff, NSFW); Claude as fallback when GPU is unreachable | Fixed monthly compute + per-call Claude. Justified when fleet traffic exceeds the per-call breakeven point (typically 3-5k jobs/month). |

**Demo deployments MUST stay on Tier 1.** The public demo's
`lintpdf-default` profile already self-skips analyzers that need a
GPU when `LINTPDF_GPU_INFERENCE_URL` is unset — codex's AI signal
extractors mirror that contract with `CODEX_AI_GPU_URL`.

#### Operator knobs (Tier 2 only)

| Env | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_GPU_URL` | unset | Optional self-hosted GPU inference URL. When unset, the GPU lane is dormant and every signal kind routes to Claude / CPU lib. |
| `CODEX_AI_GPU_AUTH_HEADER` | unset | Bearer or shared-secret token sent on every GPU request. |
| `CODEX_AI_GPU_TIMEOUT_MS` | `15000` | Per-call wall-clock cap before circuit breaker opens. |
| `CODEX_AI_GPU_DISABLED` | `false` | Hard kill-switch — temporarily route everything to Claude without removing the URL. |

#### Hosted GPU sizing recommendations (when on Tier 2)

- **Modal**: configure `min_containers=0` + `scaledown_window=180`. Idle cost ≈ $0/hour; cold-start ≈ 5-15 s on T4. **Do not set `min_containers > 0` unless your fleet sustains > 1 req/min** — the savings on cold starts evaporate against the idle bill.
- **RunPod serverless**: `max_workers` tight (≤ 4); per-call billing aligns with traffic.
- **On-prem / dedicated**: only justified above ~10k AI calls/day.

#### Per-call cost ceiling (Tier 1)

`CODEX_AI_COST_CAP_USD_PER_REQUEST` (default `0.10`): codex aborts
the extraction with a `CodexWarning(code="ai_budget_exceeded")` and
empty signal fields when projected Claude spend on a single
`/v1/extract` exceeds the cap. Same pattern lint-pdf's
`ai/cost_cap.py` already uses. Acts as a guard rail against
runaway costs on a single huge PDF.

## Canonical Codex Stack (Railway recipe)

Every Railway project that consumes codex should provision the same
set of services. Naming convention is `<purpose>-<host>` where
`<host>` is the project's short name (`lint`, `compile`, `pws`,
`codex`, `loupe`). This keeps logs + dashboards groupable across
the fleet.

### Required services

| Service name pattern | What it does | Source |
| --- | --- | --- |
| `codex-pdf-<host>-sidecar` | Codex extraction API. Read-only PDF facts. Auto-deploys from `printwithsynergy/codex-pdf` `main`. | this repo, `Dockerfile` |
| `codex-speculator-<host>` | Cache pre-warmer; subscribes to Redis stream `codex:speculate` and runs Phase 1 + 2 extracts ahead of demand. | this repo, `railway.speculator.toml` |
| `redis-<host>-managed` | Railway-managed Redis. Shared between the codex sidecar, the speculator, and (optionally) the host app's render cache. | Railway Redis template |

### Optional services

| Service name pattern | When | Notes |
| --- | --- | --- |
| `codex-vision-<host>-sidecar` | When the host needs font similarity, NSFW classification, visual diff, or perceptual-hash dedup without GPU spend. | **This is classical computer vision, not LLM-AI.** Runs deterministic ONNX models (CLIP, NudeNet) on a 2 vCPU container. No per-call LLM bill. Scale-to-zero idle. See AI Signal Campaign Phase 1.5. |

### Enterprise-only (opt-in)

| Mechanism | When | Notes |
| --- | --- | --- |
| `CODEX_AI_GPU_URL` env on `codex-pdf-<host>-sidecar` | When the host needs sub-100ms vision overlays or very high volume (>5k jobs/day). | Points at an external Modal / RunPod / on-prem GPU service. **Default unset.** Public demo deployments MUST keep it unset. |

### Per-project audit

| Project | Required services present? | Notes |
| --- | --- | --- |
| `lintpdf.com` | ✅ codex-pdf-lint-sidecar + codex-speculator + redis-lint-managed | + vision-sidecar pending Phase 1.5 |
| `compile-pdf.com` | ⚠️ missing — needs `codex-pdf-compile-sidecar` + `codex-speculator-compile` + `redis-compile-managed` | Add via Railway dashboard or `railway.toml` checked into compile-pdf-marketing/codex-sidecar/. |
| `codex-pdf-marketing` | ⚠️ presumed missing | Same checklist. Token-scope-restricted; needs separate audit. |
| `loupe-pdf-marketing` | ⚠️ unknown | Token-scope-restricted; needs separate audit. |
| Print With Synergy (production SaaS) | ❌ codex stack absent entirely. Currently only runs `lint-pdf` + `lint-pdf-ui`. | Production should be using the codex contract via a sidecar in the same project. Currently lint-pdf must be re-parsing PDFs itself. |

Operators bring deployments into compliance by:

1. Deploying `codex-pdf` (this repo, `main`) as a new Railway service named `codex-pdf-<host>-sidecar`.
2. Deploying it again with `railway.speculator.toml` as `codex-speculator-<host>`.
3. Provisioning Railway-managed Redis named `redis-<host>-managed`.
4. Setting `CODEX_REDIS_URL = "${{redis-<host>-managed.REDIS_URL}}"` on the sidecar + speculator.
5. (Optional) Adding `codex-vision-<host>-sidecar` once Phase 1.5 lands.

The host app then talks to codex over Railway's private network at `https://codex-pdf-<host>-sidecar.railway.internal`.
