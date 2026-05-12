# Unified Extraction Campaign

## North Star

Ship the unified PDF data extraction surface — text regions,
conformance verdicts, render index — through to production so every
codex consumer (preflight engines, viewer/editor frontends, batch
import pipelines) reads the same canonical contract instead of
re-parsing PDF bytes locally.

## Design Invariants

Single source of truth — do not deviate across phases.

**Consumer-agnostic surface.** No "lint_*", "loupe_*", "compile_*"
naming. Nothing in field names, headers, error shape, or wording may
presume a single consumer's pipeline.

**Two request shapes, both first-class:**
- First-stop (extract): full payload, no field selection.
- Second-stop (per-resource): narrowest possible key — one page,
  one profile, one render — for cached re-fetch.

**Cache keys are part of the contract, documented in OpenAPI:**
- `text-regions`: `(pdf_hash, page_index, dpi)`
- `conformance`:  `(pdf_hash, profile)`
- `render`:       `(pdf_hash, page_index, dpi, color_space)`

**Stage telemetry.** `X-Codex-Stage-Durations-Ms` response header AND
`stage_durations_ms` envelope field, JSON dict `{stage: ms_int}`.
Adding stage names is non-breaking; consumers treat unknown keys as
opaque.

**Additive only in v1.** No removed or renamed fields.

**Sandbox respect.** No `--no-verify`, `--no-gpg-sign`,
`--accept-data-loss`. New commits only; never amend a pushed commit.
Draft PRs only.

## Phase Plan

- [x] Phase 0 — Contract freeze — PR #14 (merged `5c8158a`),
  rc.0 cut PR #16 (merged `5a4939f`)
- [x] Phase 1 — Implementations behind stubs — PR #17
  (merged `d0a1e4d`), rc.1 cut PR #18 (merged `b98ca5b`)
- [x] Phase 1.5 — Sync-vs-async conformance decision — _resolved
  inline; see Phase 1 log_
- [x] Phase 2 — Operational contract (errors, tenancy, rate
  limits, parity) — PR #19 (merged `d79cbad`), rc.2 cut _pending_
- [x] Phase 3 — Consumer rollout + observability — PR #21
  (merged `a19a364`), rc.3 cut _pending_
- [ ] Phase 4 — Long-tail (versioning, eviction, SLOs)
- [ ] Synthesis — Emit consumer + marketing prompts

## Phase Log

### Phase 0 — 2026-05-11 — PR #14 — merged

**Shipped:**
- `CodexDocument.conformance_verdicts: dict[ConformanceProfile,
  CodexConformanceVerdict]` (additive, defaults to `{}`).
- `CodexDocument.stage_durations_ms: dict[str, int]` (envelope) +
  `X-Codex-Stage-Durations-Ms` response header on `/v1/extract`.
- `CodexPage.detected_text_regions: list[CodexDetectedTextRegion]`
  (additive, defaults to `[]`).
- New endpoint stubs (raise `NotImplementedError` → translated to
  `501 Not Implemented` via a global exception handler):
  - `GET  /v1/documents/{pdf_hash}/text-regions?page_index=N&dpi=N`
  - `POST /v1/documents/{document_id}/conformance/{profile}`
  - `GET  /v1/documents/{pdf_hash}/renders`
- New child JSON schemas under `schemas/v1/`:
  `codex-detected-text-region.schema.json`,
  `codex-conformance-verdict.schema.json`,
  `codex-clause-failure.schema.json`.
- Regenerated `codex-document.schema.json` +
  `codex-page.schema.json`; bumped `schema_version` 1.1.0 → 1.2.0.
- `/v1/contract` now lists the three new endpoints and reports
  `schema_version=1.2.0`.
- CHANGELOG Unreleased entry covers the full new surface and the
  cache-key contract.
- Behavior-locking tests: stage-durations header + envelope, the
  501 stubs, hash/profile/page/dpi validation, OpenAPI carries
  the cache-key contract inline.

**Deferred:**
- Actual implementations behind the three stubs (Phase 1).
- Tenancy / cache scoping (Phase 2).
- Documented error-shape catalogue per endpoint (Phase 2).
- Typed-client regen + bump (Phase 3) — TS client is still at
  1.8.1; will follow once the implementations stabilise.
- Pre-release tag `codex-pdf@v1.2.0-rc.0`: brief called for an
  rc.0 cut after CI green, but the PR was merged directly. Track
  whether we still want an rc cut from `5c8158a` for the internal
  registry before Phase 1 ships.

**Learned:**
- FastAPI's default handler turns `NotImplementedError` into 500.
  We added a global `@app.exception_handler(NotImplementedError)`
  that translates to 501 + JSON body so consumers get a "shape
  is right, behaviour pending" signal instead of a server error.
- The existing extract path already measured `started =
  time.perf_counter()`, so emitting the `extract` stage timing
  on the envelope + header was a near-zero-cost additive change.
- Pre-existing lint state on `api/main.py` is 24 errors (import
  sorting). This PR did not regress it; cleanup is a separate
  follow-up.

**Decisions owed:** _none — all resolved 2026-05-11; see Resolved
Questions below._

**Resolved post-merge (2026-05-11):**
- Package version bump `1.8.1` → `1.9.0-rc.0` (Python).
- TS client `@printwithsynergy/codex-client` bumped to `1.9.0-rc.0`
  with matching types + three new endpoint method stubs.
- CHANGELOG section `Unreleased` promoted to `1.9.0-rc.0 —
  2026-05-11`.
- Phase 1 cleared to proceed in parallel with lint-pdf#482's
  flag-flip.

### Phase 1 — 2026-05-11 — PR #17 — merged

**rc.1 cut:** Python `1.9.0rc1`, TS `1.9.0-rc.1` (lockstep).
Contract surface identical to `1.9.0-rc.0`; this release fills in
the three endpoint bodies. Final `1.9.0` ships after Phase 2.


**Shipped:**
- `codex_pdf.extract.text_regions` — PyMuPDF-based detector. Walks
  `page.get_text("dict")` and emits text blocks as
  `CodexDetectedTextRegion` (bbox, joined-span text, confidence,
  source=`pymupdf`) in PDF user-space points. `/v1/extract` now
  populates `CodexPage.detected_text_regions` on every page.
- `codex_pdf.extract.conformance` — verdict engine with a
  per-profile check registry. Initial coverage:
  - PDF/X-4 / X-1a / X-3: output intent + trapped flag + PDF
    version + XMP `pdfxid` (X-4).
  - PDF/A-1b / 2b / 3b: XMP packet + not-encrypted + `pdfaid:part`.
  - PDF/UA-1: XMP packet + `pdfuaid` + non-empty Title.
- `codex_pdf.api.renders_index` — side-track of the render cache.
  `POST /v1/render/page` writes `(page_index, dpi, color_space)`
  on every render; `GET /v1/documents/{pdf_hash}/renders` reads
  it back. JSON manifest under
  `codex:{VERSION}:renders-index:{pdf_hash}` — eviction follows
  the cache backend's TTL.
- All three endpoints now serve real responses (was 501). 404 on
  unknown document_id (blob missing); the cache key contract is
  unchanged.
- Stage telemetry now populates on every new endpoint: `extract`,
  `text_regions`, `conformance`, `render` slots filled with real
  wall-clock ms — both on the response envelope
  (`stage_durations_ms`) and the `X-Codex-Stage-Durations-Ms`
  header.
- Producer-surface audit allowlist extended for the new
  `codex_pdf.extract.text_regions` module (PyMuPDF read-only;
  still no PDF write paths).

**Deferred:**
- Full ISO clause coverage. Current per-profile coverage is
  3–4 hand-picked clauses each — catches the most common defects
  but is not a full conformance engine. Adding more clauses is
  a one-liner: extend `_PROFILE_CHECKS` with a
  `ConformanceCheck`. Defer to Phase 4 (long-tail).
- DPI-sensitive text region geometry. The current detector is
  DPI-independent (output is in points); `dpi` is carried in the
  cache key so a future tighter detector can vary by sampling
  fidelity without breaking the contract.
- Per-render colour space. `/v1/render/page` always records
  `color_space="sRGB"` because that's what the renderer emits.
  Separations rendering already produces other colour spaces;
  recording those into the renders index is Phase 2 work.

**Learned — conformance compute latency:**

Measured on `tests/fixtures/conforming/minimal.pdf` (n=20 hot,
n=5 cold). Numbers are milliseconds.

| profile  | hot p50 | hot p95 | cold p50 | cold p95 |
| -------- | ------- | ------- | -------- | -------- |
| pdfx4    | 0.008   | 0.042   | 12.4     | 12.9     |
| pdfx1a   | 0.005   | 0.013   | 12.7     | 13.9     |
| pdfx3    | 0.006   | 0.017   | 11.0     | 12.6     |
| pdfa1b   | 0.004   | 0.034   | 12.3     | 13.1     |
| pdfa2b   | 0.004   | 0.013   | 12.1     | 13.3     |
| pdfa3b   | 0.004   | 0.010   | 11.0     | 12.0     |
| pdfua1   | 0.004   | 0.013   | 10.0     | 11.5     |

Hot path is the predicate registry alone (CodexDocument already
parsed). Cold path includes a fresh `extract_document` — the
dominant cost — which the endpoint amortises by hitting the
extract cache from `/v1/extract`. Both bands are orders of
magnitude below the 2s threshold the playbook set for Phase 1.5.

### Phase 1.5 — 2026-05-11 — Sync-vs-async decision

**Decision: keep synchronous.** Every profile's measured p95
(both hot and cold) lands well under the 2-second threshold from
the playbook. The minimum-coverage check registry runs in
microseconds when the doc is already cached, and the cold path is
≤ 14 ms end-to-end — dominated by the extract parse, not the
verdict math. An async job pattern would add coordination cost
(202-then-poll, job state, eviction) for no latency win.

Revisit if a future ISO clause adds a heavy probe (e.g. Type-4
function evaluation, ICC profile validation against the printer
reference) and pushes p95 over the threshold.

**Re-evaluation trigger:** if any profile's p95 exceeds 500 ms
(quarter of the threshold) on a representative fixture corpus,
flip Phase 1.5 back to open and propose the 202-job pattern.

## Open Questions

_None blocking._ Q1–Q3 below were resolved on 2026-05-11; kept in
the log for traceability.

### Resolved

- **Q1 (resolved 2026-05-11):** Pre-release tag policy. Decision:
  **cut `codex-pdf@v1.9.0-rc.0`** from the Phase 0 merge (next
  package minor — previous was `1.8.1`). Gives consumers a
  pinnable pre-release of the contract surface while stubs
  return 501; final `1.9.0` ships when Phase 1 fills them.
- **Q2 (resolved 2026-05-11):** TS client lockstep. Decision:
  **bump `@printwithsynergy/codex-client` to `1.9.0-rc.0`**
  alongside Python. Adds matching types
  (`DetectedTextRegion`, `ConformanceVerdict`, `ClauseFailure`,
  `ConformanceProfile`) and three new endpoint methods
  (`getTextRegions`, `computeConformance`, `listRenders`).
- **Q3 (resolved 2026-05-11):** Consumer interlock with
  lint-pdf#482. Decision: **proceed with Phase 1 in parallel.**
  lint-pdf consumer side is already merged behind a flag; they
  can flip when ready. Phase 1 is additive — the contract shape
  doesn't change.

### Phase 2 — 2026-05-11 — PR #19 — merged

**rc.2 cut:** Python `1.9.0rc2`, TS `1.9.0-rc.2` (lockstep).
No new endpoints; contract surface identical to `1.9.0-rc.1`
on the response shape. Cache-key shape changed deliberately —
operators upgrading from rc.1 see cold caches on first request.


**Shipped:**
- **Tenancy.** ``cache_key`` now keys on
  ``(VERSION, kind, tenant, pdf_hash, args_sha)``. ``_blob_store``
  and ``renders_index`` both gained explicit ``tenant`` parameters.
  Every endpoint that touches the cache or blob store derives the
  tenant from the ``X-Codex-Tenant`` header via
  ``normalise_tenant`` and threads it through. Default fallback is
  ``"default"``. The 412 message on a hash miss is intentionally
  identical for "wrong tenant" and "expired" so probing for a
  hash's owner is uninformative.
- **Error-shape catalogue.** New shared ``ErrorResponse`` envelope
  ``{detail: str}``. Phase 1 endpoints declare
  ``responses={400, 404, 429}`` in their FastAPI decorators with
  the unified shape so OpenAPI surfaces the catalogue. Older
  endpoints keep their existing ``HTTPException`` flow (same
  envelope, no decorator change yet — Phase 4 cleanup).
- **Rate limits.** New ``codex_pdf.api.rate_limit`` module: simple
  in-process token bucket per ``(tenant, endpoint)``. Compute-and-
  cache POSTs (`extract`, `extract_stream`, `render_page`,
  `render_separations`, `render_heatmap`, `render_layer`,
  `sample_color`, `sample_density`, `walk_content_stream`,
  `conformance`) all consult the limiter and emit
  ``429 Too Many Requests`` + ``Retry-After`` when the bucket is
  empty. Env config: ``CODEX_RATE_LIMIT_RPM`` (default 120),
  ``CODEX_RATE_LIMIT_BURST`` (default 30),
  ``CODEX_RATE_LIMIT_DISABLED`` (off-switch).
- **Behavior-locking parity test.** Snapshots the 1.0-era field
  set on ``/v1/extract`` and asserts no removed/renamed fields at
  document- or page-level. Future contract changes that aren't
  additive trip this test loudly.

**Deferred:**
- Multi-replica rate limiting. The in-process limiter is per-
  replica; effective limit on N replicas is N × rpm. Phase 4
  (long-tail) will move to Redis-backed counters if/when we need
  fleet-wide quotas.
- Per-endpoint quota policy. Every limited endpoint shares the
  same bucket sizes. Per-endpoint overrides (e.g. cheaper limit on
  expensive `extract_stream`) can layer additively.
- Machine-readable error ``code`` field. Current ``ErrorResponse``
  is ``{detail: str}``; adding ``code`` later is additive (no
  field rename / removal).
- Retrofit of older endpoints' ``responses=`` decorators. The
  shape they emit is already ``ErrorResponse``-compatible (FastAPI
  ``HTTPException`` → ``{"detail": "..."}``), but their OpenAPI
  description still lists generic 500s. Phase 4 cleanup.

**Learned:**
- Test isolation matters. The fastapi ``TestClient`` shares
  module-level state (``_blob_store``, ``_cache``,
  ``_rate_limiter``) across tests; we can't make assertions about
  the default tenant from a single test because prior tests have
  already populated it. Future cross-tenant tests should use
  unique tenant labels (e.g. ``"tenant-a"`` / ``"tenant-b"``)
  rather than relying on the default.
- Cache-key shape change is a deliberate break, OK because we're
  pre-release. All ``codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}``
  keys are now ``codex:{VERSION}:{kind}:{tenant}:{pdf_sha}:{args_sha}``.
  Operators upgrading from rc.1 → rc.2/final will see cold caches
  on first request; that's the price of multi-tenant correctness.

**Decisions owed:** _none_.

## Synthesis Output

Not yet produced. Populated by the `synthesize` invocation once
implementations stabilise. Phase 2 is now the right moment — rc.2
(or 1.9.0 final) makes the operational contract concrete for
consumers to wire against.

### Phase 3 — 2026-05-12 — PR #21 — merged

**rc.3 cut:** Python `1.9.0rc3`, TS `1.9.0-rc.3` (lockstep).
No contract change vs rc.2; this release ships the bundled
clients + observability surfaces.


**Shipped:**
- **Python client tenant + Phase 1 surface.** ``HttpClient``
  constructor gained a ``tenant`` keyword (env fallback
  ``CODEX_TENANT``) and surfaces it as ``X-Codex-Tenant`` on every
  request. Three new methods: ``text_regions(pdf_hash, ...)``,
  ``conformance(document_id, profile)``, ``list_renders(pdf_hash)``.
  ``extract()`` back-fills ``stage_durations_ms`` from the
  ``X-Codex-Stage-Durations-Ms`` header when the envelope omits
  it. 429 handling now honours ``Retry-After`` over the
  exponential backoff.
- **TS client tenant.** ``CodexClientOptions.tenant`` (env
  fallback ``CODEX_TENANT``) threaded through ``headers()``; 429
  retry honours ``Retry-After``. Existing Phase 1 methods
  (``getTextRegions`` / ``computeConformance`` / ``listRenders``)
  added in rc.0 are unchanged — they already use the same
  request path.
- **Cache-key stability test.** Subprocess-based test asserts
  ``cache_key`` is a pure function of its inputs — same inputs in
  a fresh Python process yield the same key bytes. Catches
  accidental dependence on module-level state.
- **Cache hit/miss + stage observability.** New Prometheus
  surfaces:
  - ``codex_api_cache_lookups_total{endpoint, outcome=hit|miss}``
  - ``codex_api_stage_seconds{stage}``
  Both Phase 1 endpoints (`text_regions`, `conformance`),
  `/v1/extract`, and the renders index emit these. The stage
  histogram mirrors `stage_durations_ms` for Grafana parity with
  the consumer-visible numbers.
- **Integration guide.** ``docs/unified-extraction.md`` covers
  endpoints, cache-key contract, tenancy, rate limiting, error
  shapes, stage telemetry, observability, conformance profiles,
  and an end-to-end Python + TS example. Single source consumers
  can paste into their wiki.

**Deferred:**
- Bulk OpenAPI ``responses=`` cleanup for older endpoints. Their
  shape is already ``ErrorResponse``-compatible (FastAPI
  ``HTTPException``) but the OpenAPI doc still lists generic
  defaults. Defer to Phase 4 cleanup.
- Generated SDKs from OpenAPI (e.g. Go, Ruby). Hand-rolled Python
  + TS already cover the two named consumers; spin up a generated
  SDK lane only if a new consumer arrives in a different
  language.
- Cache hit-rate dashboards. The metrics ship; Grafana JSON is
  operator-owned and lives outside this repo.

**Learned:**
- The bundled clients had drifted from the server contract: the
  Python client predated tenant scoping by months and the TS
  client never had it. Lockstep bumps with the server keep this
  drift visible in CI.
- Cache hit/miss counters at the per-endpoint level cost ~zero
  (prometheus-client is in-process) and make the
  "is the cache earning its keep?" question answerable from a
  single dashboard panel. Worth doing in Phase 1; we delayed it
  to Phase 3 to keep PR diffs narrow.

**Decisions owed:** _none_.

## Next Phase — Plan (for `next` invocation)

**Phase 4 — Long-tail.**

- ``ConformanceProfile`` enum versioning policy. Document how
  consumers handle unknown profile keys (already the contract;
  formalise the SLA).
- Cache eviction / TTL policy. Today the cache backends (memory
  LRU; Redis SETEX) have their own TTLs; centralise the policy
  knob.
- Backpressure on conformance compute queue. Currently the rate
  limiter sheds excess load; we may want a 503 with backpressure
  semantics for downstream fairness.
- SLOs published. Define + publish p95 latency + availability
  SLOs per endpoint; wire alerts.

No blockers. Phase 3 is complete: consumers can wire against a
documented surface today using `1.9.0-rc.3` (cut after this PR
merges) or the eventual `1.9.0` final.

**Synthesis is now eligible.** With Phase 3 complete, the
playbook's `synthesize` invocation can run to emit wave-ordered
consumer + marketing-site prompts. Synthesis was deferred until
"impls stabilise"; they have.
