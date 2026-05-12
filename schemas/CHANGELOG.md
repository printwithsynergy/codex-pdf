# Schema Changelog

## 1.9.0-rc.2 — 2026-05-12

Phase 2 of the unified extraction campaign — operational contract
hardening. No new endpoints; this release tightens what the
existing endpoints do under load and across tenants. Python
`codex-pdf` and TypeScript `@printwithsynergy/codex-client` ship
in lockstep at `1.9.0-rc.2`.

### Tenancy

Cache lookups are now scoped by tenant. Cache key shape changes
from
``codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}``
to
``codex:{VERSION}:{kind}:{tenant}:{pdf_sha}:{args_sha}``
The ``X-Codex-Tenant`` request header selects the tenant; missing
or malformed values fall back to ``"default"``. This applies to
the blob store and the renders index as well — a hash uploaded
by Tenant A is invisible to Tenant B even if the hash is known.

This is a deliberate cache-key break and acceptable on pre-release.
Operators upgrading from ``rc.1`` will see cold caches on first
request after the deploy.

### Error-shape catalogue

Shared ``ErrorResponse`` envelope (``{"detail": "..."}``) for all
4xx/5xx responses. Phase 1 endpoints (text-regions, conformance,
renders) document their per-status error shapes in OpenAPI under
``responses=``. Consumers can drive UI states off the documented
catalogue without trial-and-error.

### Rate limits

Compute-and-cache POSTs (extract, render, sample, walk,
conformance) consult a process-wide token bucket per ``(tenant,
endpoint)``. Bucket is exhausted → ``429 Too Many Requests`` with
a ``Retry-After`` header. Env knobs:

- ``CODEX_RATE_LIMIT_RPM`` (default ``120``)
- ``CODEX_RATE_LIMIT_BURST`` (default ``30``)
- ``CODEX_RATE_LIMIT_DISABLED`` (default ``false``)

The limiter is in-process and per-replica. Multi-replica fleets
get effective limit ``N × rpm``; a distributed accounting backend
is on the long-tail backlog.

### Behavior-locking parity

A new test pins the 1.0-era field set on ``/v1/extract`` and
asserts no removed/renamed fields. Future contract changes that
aren't additive trip this test loudly.

## 1.9.0-rc.1 — 2026-05-11

Phase 1 of the unified extraction campaign — the three per-resource
endpoints stop returning 501 and start serving real data. Python
`codex-pdf` and TypeScript `@printwithsynergy/codex-client` ship in
lockstep at `1.9.0-rc.1`. The contract surface is identical to
`1.9.0-rc.0`; this release fills in the bodies. Final `1.9.0` ships
after Phase 2 hardens the operational contract (error shapes,
tenancy, rate limits).

### Implementations behind the stubs

- `GET /v1/documents/{pdf_hash}/text-regions` — PyMuPDF-based
  detector; geometry in PDF user-space points. Idempotent under the
  cache key `(pdf_hash, page_index, dpi)`.
- `POST /v1/documents/{document_id}/conformance/{profile}` — verdict
  engine with a hand-curated check registry per profile. Cached
  under `(pdf_hash, profile)`. Idempotent.
- `GET /v1/documents/{pdf_hash}/renders` — reads a side-track that
  `POST /v1/render/page` writes on every render. Lists cached
  `(page_index, dpi, color_space)` tuples.

Unknown document hash returns `404 Not Found` (was `501`) — upload
via `/v1/extract` first or pass raw bytes.

### Stage telemetry now populated

Every new endpoint emits real wall-clock ms in `stage_durations_ms`
(envelope + `X-Codex-Stage-Durations-Ms` header). Initial slots:
`extract`, `text_regions`, `conformance`, `render`.

### Extract response gains regions inline

`/v1/extract` now populates `CodexPage.detected_text_regions` on
every page on its way out, so consumers receive regions in the
first-stop response without a follow-up call.

### Cache-key contract unchanged

Cache keys for all three endpoints are still as documented in
`1.9.0-rc.0`. Phase 1 fills in the bodies; the contract surface is
the same.

## 1.9.0-rc.0 — 2026-05-11

First release candidate of the unified extraction contract. Python
package `codex-pdf` and TypeScript client
`@printwithsynergy/codex-client` ship in lockstep at `1.9.0-rc.0`.

Contract schema bump: `1.1.0` → `1.2.0` (additive, fully backward
compatible — no removed or renamed fields). The codex package
version moves from `1.8.1` → `1.9.0-rc.0`; the next package minor
matches the new contract surface area even though the contract
itself is identified by `schema_version`.

Unified extraction API — public contract for the first-stop extract
endpoint plus a set of per-resource second-stop endpoints so
consumers (preflight engines, viewer/editor frontends, batch import
pipelines) can fetch exactly the slice they need without an
extract-then-discard round trip. Consumer-agnostic by design — no
field, header, or path component assumes a specific caller.

Schema bump: `1.1.0` → `1.2.0` (additive, fully backward compatible —
no removed or renamed fields).

### New top-level surface

- `CodexDocument.conformance_verdicts: dict[ConformanceProfile,
  CodexConformanceVerdict]` — empty until requested. Profile keys
  are forward-compatible: `pdfx4`, `pdfx1a`, `pdfx3`, `pdfa1b`,
  `pdfa2b`, `pdfa3b`, `pdfua1`. Consumers must treat unknown keys
  as opaque.
- `CodexDocument.stage_durations_ms: dict[str, int]` — per-stage
  wall-clock telemetry in milliseconds. Mirrored on the
  `X-Codex-Stage-Durations-Ms` response header so transports that
  strip headers (in-process clients, mocks) still surface it.
  Initial stage names: `extract`, `render`, `text_regions`,
  `conformance`. Adding new ones is non-breaking.
- `CodexPage.detected_text_regions: list[CodexDetectedTextRegion]` —
  populated whenever codex computed regions for the page during
  extraction; empty otherwise. Geometry is in PDF user-space points.

### New child schemas (`schemas/v1/`)

- `codex-detected-text-region.schema.json` — `bbox`, `text`,
  `confidence`, `polygon`, `source`.
- `codex-conformance-verdict.schema.json` — `passed`, `clauses`.
- `codex-clause-failure.schema.json` — `clause`, `test_number`,
  `description`, `failed_check_count`.

### New endpoints (stub in this release)

- `GET /v1/documents/{pdf_hash}/text-regions?page_index=N&dpi=N` —
  second-stop re-fetch of one page's regions, scaled to PDF points.
  Cache key: `(pdf_hash, page_index, dpi)`.
- `POST /v1/documents/{document_id}/conformance/{profile}` —
  compute and cache a conformance verdict for the given profile.
  Cache key: `(pdf_hash, profile)`. Idempotent: a second call
  returns the cached verdict bit-for-bit.
- `GET /v1/documents/{pdf_hash}/renders` — list `(page_index, dpi,
  color_space)` tuples that are already in the render cache for
  this PDF so consumers can skip re-requests. Render cache key
  remains `(pdf_hash, page_index, dpi, color_space)`.

All three endpoints raise `NotImplementedError` in this release;
the public contract — request shape, cache keys, response shape —
is published so consumers can wire against the surface ahead of
the rollout. The handler returns `501 Not Implemented` with a
JSON body.

### Cache-key contract (stable across versions)

- `text-regions`: `(pdf_hash, page_index, dpi)`
- `conformance`: `(pdf_hash, profile)`
- `render`: `(pdf_hash, page_index, dpi, color_space)`

These are part of the contract — they will not change between
versions. They are also documented inline on the OpenAPI
description for each endpoint.

### Backward compatibility

- No removed or renamed fields. All new fields default to empty
  collections or are absent.
- `POST /v1/extract` continues to accept callers that don't yet
  read the new fields. The response is a strict superset of the
  prior shape.

## 1.0.0

- Initial public `CodexDocument` contract.
- Added root and child schema files under `schemas/v1/`:
  - output intents
  - color spaces and spot colorants
  - fonts
  - images
  - OCGs
  - form XObjects
  - annotations
  - preflight reports/issues
  - trap evidence
- Introduced SemVer governance:
  - patch: non-breaking clarifications
  - minor: additive fields only
  - major: breaking field changes
