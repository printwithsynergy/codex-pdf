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

- [x] Phase 0 — Contract freeze — PR #14 (merged `5c8158a`)
- [ ] Phase 1 — Implementations behind stubs
- [ ] Phase 1.5 — Sync-vs-async conformance decision
- [ ] Phase 2 — Operational contract (errors, auth, rate limits)
- [ ] Phase 3 — Consumer rollout + observability
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

**Decisions owed:**
- Tag policy: cut `codex-pdf@v1.2.0-rc.0` from `5c8158a` now, or
  wait until Phase 1 lands and ship `v1.2.0` final?
- Consumer interlock: lint-pdf#482 (commit `cc65087b`) reads via
  the `CodexClient` abstraction behind a flag. Do we wait for
  their CI / flag-flip before opening Phase 1, or run in parallel?

## Open Questions

- **Q1:** Cut `codex-pdf@v1.2.0-rc.0` from `5c8158a` for the
  internal registry? — owner: human — blocks: Phase 1 publishing
  pipeline.
- **Q2:** Bump `@printwithsynergy/codex-client` (TS) to `1.2.0-rc.0`
  in lockstep, or wait until Phase 3? — owner: human — blocks:
  Phase 3 consumer rollout.
- **Q3:** Consumer interlock with lint-pdf#482 — wait for their
  flag-flip green-light or proceed in parallel? — owner: human —
  blocks: Phase 1 scope sequencing.

## Synthesis Output

Not yet produced. Populated by the `synthesize` invocation once
implementations stabilise (post Phase 1, latency permitting).

## Next Phase — Plan (for `next` invocation)

**Phase 1 — Implementations behind stubs.** Scope:

- Wire the text-region detector to the cache key
  `(pdf_hash, page_index, dpi)`; have `/v1/extract` populate
  `CodexPage.detected_text_regions` when computed and serve the
  `GET /v1/documents/{pdf_hash}/text-regions` endpoint from cache.
- Implement the conformance engine for each enum profile
  (`pdfx4`, `pdfx1a`, `pdfx3`, `pdfa1b`, `pdfa2b`, `pdfa3b`,
  `pdfua1`); cache by `(pdf_hash, profile)`; serve from
  `POST /v1/documents/{document_id}/conformance/{profile}`.
- Implement `GET /v1/documents/{pdf_hash}/renders` by indexing
  what's already in the render cache for that PDF.
- Emit real per-stage timings on every response — fill the
  `extract` / `render` / `text_regions` / `conformance` slots in
  `stage_durations_ms` (the envelope + header are already wired).
- Instrument latency for conformance compute per profile; record
  p50/p95 in the Phase 1 log entry. This number drives Phase 1.5.

Open Questions Q1–Q3 do not strictly block Phase 1 from starting,
but they should be resolved before merging Phase 1 so the rollout
order is unambiguous. Surface them again at the top of the Phase 1
PR description.
