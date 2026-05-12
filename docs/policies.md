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
