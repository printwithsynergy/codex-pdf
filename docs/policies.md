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

### Warning catalogue

| `code` | `scope` | Meaning |
| --- | --- | --- |
| `ai_disabled` | `signals.ai` | Operator gate is off. Affects every request. |
| `ai_skipped` | `signals.ai` | Caller opted out for this request. |
| `ai_signals_pending_impl` | `signals.ai` | AI is enabled but the Phase 1 implementation isn't deployed yet. Phase 0 advisory only. |

Exactly one of these always lands on every `/v1/extract` response
when AI is requested or pending. Consumers MUST NOT branch on the
absence of these warnings — branch on the presence of the
specific code instead.

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
