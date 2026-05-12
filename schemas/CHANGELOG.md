# Schema Changelog

## 1.15.0 — 2026-05-12

Dieline.count / dieline.size reconciliation. **Schema unchanged at
1.3.0**.

### Behaviour change

When the bbox-based geometry-fallback in
``_extract_dieline_metrics`` produces a real ``dieline.size``
(source ``analysis_stroke_bbox``) but no named candidate hit any
of the registry-driven paths, codex now synthesises a placeholder
``CodexSummaryDielineCandidate`` so ``dieline.count`` agrees with
``dieline.size`` and consumers don't see "Detected dieline size
4.98 x 6.53 in" alongside "Dieline candidates: 0".

### Forward-compatible literal extensions

- ``CodexSummaryDielineCandidate.source`` literal gains
  ``"analysis_stroke_bbox"``.
- ``CodexSummaryDielineCandidate.reason_codes`` literal gains
  ``"geometry_fallback_size_detected"``.

Per `docs/policies.md`'s forward-compatibility rule, consumers MUST
treat the Literal unions as open enums.

## 1.13.0 — 2026-05-12

Phase 4 — AI signal model versioning + SLOs. **Schema unchanged at
1.3.0**; adds advisory metadata.

### New /v1/contract surface

- ``ai_model_versions: dict[str, dict[str, str]]`` mirrors
  ``codex_pdf.ai.versions.AI_MODEL_VERSIONS``. SDK consumers pin
  against the exact extractor that produced a signal. Bump the
  per-kind ``prompt`` constant whenever the system prompt changes
  so consumers can invalidate stale caches deliberately.

### New Prometheus metric

- ``codex_ai_signal_calls_total{kind, model, status}`` counter
  on ``/metrics``. Operators chart per-kind success rate / cost-cap
  hits / model rollover drift.

### Docs

- `docs/slos.md` gains an "AI signal SLOs" section covering
  latency bands, cost cap targets, per-extractor success-rate
  floors.

## 1.14.0 — 2026-05-12

Codex AI Signal Campaign — Phase 2 (operational contract). Per-
tenant entitlements for the AI signal lane. **Schema unchanged at
1.3.0**.

### New env knobs

| Env | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_TENANTS_ALLOWLIST` | unset | Comma-separated tenant slugs. When set, ONLY these tenants can run AI; everyone else gets `ai_tenant_excluded`. |
| `CODEX_AI_TENANTS_DENYLIST` | unset | Comma-separated tenant slugs blocked from AI. Allowlist wins when both are set. |

### New warning code

- `ai_tenant_excluded` — operator opted in but the requesting
  tenant is gated out by allowlist / denylist. Signal fields stay
  empty.

### Cache tenancy

Per-kind cache keys (`codex:{VERSION}:signal:{tenant}:{pdf_hash}:{kind}[:p{idx}]`)
already namespace by tenant. Cross-tenant isolation was an
invariant of the 1.10.0 contract; Phase 2 just adds the test
that proves tenant A's signal data never leaks to tenant B even
when they upload identical PDFs.

## 1.12.0 — 2026-05-12

Codex AI Signal Campaign — Phase 1.5 (codex-vision-sidecar). New
service surface for CPU-only computer-vision extractors that
complement Phase 1's Claude-backed lane. **Schema unchanged at
1.3.0**; this release adds infrastructure, not contract.

### New service surface

- New package `codex_pdf.vision/` with `app.py` (FastAPI service),
  `phash.py` (perceptual hashing, no ML model file), and
  `client.py` (HTTP client the main API uses to call into the
  sidecar).
- New entrypoint `python -m codex_pdf.vision` for the sidecar
  service.
- New Railway config `railway.vision.toml` — same docker image as
  the main codex-pdf service, only the start command differs.
- New independent contract version constant
  `codex_pdf.vision.VISION_SCHEMA_VERSION = "1.0.0"`.

### New endpoints (on the vision sidecar service, NOT on the main API)

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness + extractor inventory. |
| `GET /v1/contract` | Endpoint inventory + schema versions. |
| `POST /v1/vision/phash` | Multipart PNG → 64-bit pHash hex. |

### New env vars

| Env | Default | Where | Meaning |
| --- | --- | --- | --- |
| `CODEX_VISION_URL` | unset | main API | Vision sidecar private-network URL. Unset → vision lane dormant. |
| `CODEX_INTERNAL_TOKEN` | unset | both | Shared secret; sidecar enforces it when set. |

### New optional extra

- `pip install "codex-pdf[vision]"` adds `imagehash` + `Pillow`.

## 1.11.0 — 2026-05-12

Codex AI Signal Campaign — Phase 1 (implementation lands). The six
extractors frozen by 1.10.0's contract are now wired and produce
real data. **Schema unchanged at 1.3.0** — the contract was
finalised in 1.10.0; 1.11.0 ships the runtime behind it.

### Behaviour change (additive)

- `GET /v1/documents/{pdf_hash}/signals/{kind}` now returns real
  data instead of `501 Not Implemented`. Pass `?page_index=N` for
  page-scoped kinds (default `0`); `classification` is
  document-scoped and ignores the parameter. The 404 contract for
  uncached PDFs is unchanged.
- `POST /v1/extract` populates the five page-scoped signal fields
  and `document_classification` when `CODEX_AI_ENABLED=true` and
  the caller has not opted out via `X-Codex-Skip-AI: true`.

### Warning catalogue

- `ai_signals_pending_impl` is removed — Phase 0 only.
- `ai_missing_credentials` is new — operator opted in but the
  `anthropic` SDK isn't importable or `ANTHROPIC_API_KEY` is unset.
- `ai_tier` now emits on every successful AI run; the `message`
  carries the tier label (`cpu+claude` for Tier 1, `gpu` for
  Tier 2) plus the realised dollar spend.
- `ai_budget_exceeded` is unchanged but now actually emits — the
  per-request cost cap (`CODEX_AI_COST_CAP_USD_PER_REQUEST`,
  default `$0.10`) is enforced by `codex_pdf.ai.budget.AiBudget`.

### New / changed operator switches

| Env | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_COST_CAP_USD_PER_REQUEST` | `0.10` | Per-request hard cap on projected Claude spend. The next call's projection is checked BEFORE the call goes out. |
| `ANTHROPIC_API_KEY` | unset | Required when `CODEX_AI_ENABLED=true`. Unset emits `ai_missing_credentials`. |
| `CODEX_AI_GPU_URL` | unset | Optional Tier 2 GPU endpoint. Phase 1.5 will wire the lane; Phase 1 reads but doesn't dispatch. |

### Cache + tier

- Per-kind cache namespace is `codex:{VERSION}:signal:{tenant}:{pdf_hash}:{kind}[:p{idx}]`.
  Idempotent: same key → same JSON.
- Default backend in Phase 1 is **Tier 1 (CPU + Claude)** as
  designed; `CODEX_AI_GPU_URL` stays unset on the public demo.

## 1.10.0 — 2026-05-12

Codex AI Signal Campaign — Phase 0 (contract freeze). Additive
fields + new per-resource endpoint for AI-derived detection signals
that move from lint-pdf's `AI_*` rule namespace into codex's
data-collection layer. Schema bump `1.2.0` → `1.3.0` (additive,
fully backward compatible).

Two backends documented: Tier 1 (CPU + Claude only — default,
demo, OSS) and Tier 2 (optional GPU — SaaS / Enterprise via
`CODEX_AI_GPU_URL`). Public demo MUST stay on Tier 1. See
`docs/policies.md` > "Two AI backends".

### New top-level surface

- `CodexPage.detected_language: CodexDetectedLanguage | None` —
  BCP-47 tag + confidence + source.
- `CodexPage.detected_logos: list[CodexDetectedLogo]` — bbox +
  optional canonical identity.
- `CodexPage.detected_symbols: list[CodexDetectedSymbol]` — bbox +
  kind (e.g. `ghs_flammable`, `recycle_pet`, `fda_drug_facts`).
- `CodexPage.detected_barcodes: list[CodexDetectedBarcode]` —
  bbox + format + decoded value.
- `CodexPage.spell_candidates: list[str]` — unknown-word list, no
  tenant dictionary policy.
- `CodexDocument.document_classification: dict[str, float]` —
  per-category probabilities (`prescription_drug`, `folding_carton`,
  `sign`, `proof`, …).

All default to empty / null until the operator enables AI via
`CODEX_AI_ENABLED=true` and the caller does **not** opt out via
`X-Codex-Skip-AI: true`.

### New endpoint (stub in this release)

- `GET /v1/documents/{pdf_hash}/signals/{kind}` —
  per-resource second-stop fetch. Cache key:
  `(tenant, pdf_hash, kind)`. Valid kinds: `language`, `logos`,
  `symbols`, `barcodes`, `spell`, `classification`.
- Raises `NotImplementedError` → `501 Not Implemented` in this
  release. Phase 1 fills the body.

### Operator + caller opt-out

| Surface | Default | Meaning |
| --- | --- | --- |
| `CODEX_AI_ENABLED` env | `false` | Operator switch. When unset / false, AI extraction never runs. |
| `X-Codex-Skip-AI` header | absent | Caller switch. When `true`, AI extraction is skipped for this request even if the operator has it on. |

### CodexWarning codes

`POST /v1/extract` now emits exactly one of these warnings in
`extraction_warnings` so consumers can render an honest "AI signals
not available" state instead of guessing:

| `code` | `scope` | Meaning |
| --- | --- | --- |
| `ai_disabled` | `signals.ai` | Operator hasn't set `CODEX_AI_ENABLED=true`. AI fields remain empty regardless of caller intent. |
| `ai_skipped` | `signals.ai` | Caller sent `X-Codex-Skip-AI: true`. AI fields remain empty. |
| `ai_signals_pending_impl` | `signals.ai` | AI is enabled and caller did not opt out, but the Phase 1 implementation is not deployed yet. Phase 0 always sets this. |

### Cache-key contract additions

- `language`:       `(pdf_hash, page_index, "language")`
- `logos`:          `(pdf_hash, page_index, "logos")`
- `symbols`:        `(pdf_hash, page_index, "symbols")`
- `barcodes`:       `(pdf_hash, page_index, "barcodes")`
- `spell`:          `(pdf_hash, page_index, "spell")`
- `classification`: `(pdf_hash, "classification")`

Stable across versions; documented inline on the OpenAPI
description for `/v1/documents/{pdf_hash}/signals/{kind}`.

## 1.9.0 — 2026-05-12

Final cut of the unified extraction campaign. All four planned
phases (contract freeze → implementations → operational contract
→ long-tail policies) are complete and deployed. Python
`codex-pdf` and TypeScript `@printwithsynergy/codex-client` ship
in lockstep at `1.9.0`.

This release supersedes the rc series (rc.0 → rc.3) and is
promoted to the default channel:

- PyPI: `codex-pdf==1.9.0` (default resolution, no `--pre`).
- npm: `@printwithsynergy/codex-client@1.9.0` on the `latest`
  dist-tag.

The contract surface is identical to `1.9.0-rc.3`. Phase 4's
``CODEX_CACHE_TTL_SECONDS`` knob plus ``docs/policies.md`` +
``docs/slos.md`` rounded out the operator-facing SLAs.

### What 1.9.0 ships (summary)

- Unified extraction contract: `/v1/extract` as first-stop;
  per-resource second-stop endpoints
  (`GET /v1/documents/{pdf_hash}/text-regions`,
  `POST /v1/documents/{document_id}/conformance/{profile}`,
  `GET /v1/documents/{pdf_hash}/renders`).
- Stage telemetry on every response
  (`stage_durations_ms` + `X-Codex-Stage-Durations-Ms`).
- Tenancy scoping on cache + blob store + renders index via
  `X-Codex-Tenant`.
- Rate limiting (`429 + Retry-After`) on compute-and-cache
  POSTs. Knobs: `CODEX_RATE_LIMIT_RPM`,
  `CODEX_RATE_LIMIT_BURST`, `CODEX_RATE_LIMIT_DISABLED`.
- Bundled Python + TS clients with the full surface
  (tenant option, new methods, Retry-After awareness).
- Cache hit/miss + per-stage Prometheus metrics.
- Cache TTL knob (`CODEX_CACHE_TTL_SECONDS`).
- Published policy SLAs (`docs/policies.md`,
  `docs/unified-extraction.md`) and SLOs (`docs/slos.md`).

See the rc series entries below for per-phase detail.

## 1.9.0-rc.3 — 2026-05-12

Phase 3 of the unified extraction campaign — consumer rollout +
observability. No new endpoints; this release brings the bundled
clients up to the server contract and adds the metrics consumers
need to wire dashboards. Python `codex-pdf` and TypeScript
`@printwithsynergy/codex-client` ship in lockstep at `1.9.0-rc.3`.

### Bundled clients now ship the Phase 1/2 surface

- **Python** (`codex_pdf.client.HttpClient`): `tenant` constructor
  keyword + `CODEX_TENANT` env, surfaces as `X-Codex-Tenant`
  on every request. New methods `text_regions`, `conformance`,
  `list_renders` for the per-resource endpoints. `extract()`
  back-fills `stage_durations_ms` from the
  `X-Codex-Stage-Durations-Ms` header. 429 retries honour
  `Retry-After` over the exponential backoff.
- **TypeScript** (`@printwithsynergy/codex-client`):
  `CodexClientOptions.tenant` + env fallback; same header. 429
  retries honour `Retry-After`. Phase 1 methods
  (`getTextRegions`, `computeConformance`, `listRenders`) already
  shipped in rc.0.

### Cache hit-rate + per-stage observability

New Prometheus surfaces on `/metrics`:

- `codex_api_cache_lookups_total{endpoint, outcome=hit|miss}` —
  cache hit rate per endpoint.
- `codex_api_stage_seconds{stage}` — mirrors `stage_durations_ms`
  so Grafana panels can use the same numbers consumers see in
  responses.

### Cache-key stability test

Subprocess-based test asserts `cache_key()` is a pure function of
its inputs — same inputs in a fresh Python process yield the same
key bytes. Catches accidental dependence on module-level state.

### Integration guide

`docs/unified-extraction.md` documents the endpoints, cache-key
contract, tenancy, rate limiting, error shapes, stage telemetry,
observability, conformance profiles, and end-to-end Python + TS
examples. Single source consumers can paste into their wiki.

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
