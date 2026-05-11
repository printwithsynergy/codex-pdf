# Schema Changelog

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
