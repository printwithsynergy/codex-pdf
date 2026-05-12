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
- [x] Phase 4 — Long-tail (versioning, eviction, SLOs) — PR #23
  (merged `3a774b9`), `1.9.0` final cut _pending_
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

_Produced 2026-05-12 — post `1.9.0` cut._

### Discovery summary

| Repo | Consumes | Current pin | Classification |
| --- | --- | --- | --- |
| `codex-pdf` | (self) | n/a | Wave 1 — DONE (`1.9.0` on PyPI + npm `latest`). |
| `codex-pdf-marketing` | `@printwithsynergy/codex-client` | `^1.7.0` | Wave 1 — FLAG_FLIP. |
| `loupe-pdf` | _no codex deps_ | n/a | Wave 2 — **NO_OP**. |
| `loupe-pdf-marketing` | `@printwithsynergy/codex-client` | `^1.6.1` (caret auto-accepts 1.9.0) | Wave 2 — **NO_OP**. |
| `lint-pdf` | `codex-pdf` | `>=1.4.4` | Wave 3 — INTEGRATE (flag-flip already prepared in lint-pdf#482). |
| `compile-pdf` | `codex-pdf` | `>=1.8.1,<2.0` | Wave 3 — FLAG_FLIP. |
| `lint-pdf-marketing` | `@printwithsynergy/codex-client` | `1.8.1` (exact) | Wave 3 — FLAG_FLIP. |
| `compile-pdf-marketing` | `@printwithsynergy/codex-client` | `^1.8.1` | Wave 3 — FLAG_FLIP. |

**Loupe is NO_OP** — Wave 2 collapses. Wave 3 preconditions
reference only the codex bump (no loupe version gate).

### Wave-grouped summary

```
Wave 1 — Codex (done) + codex marketing
  printwithsynergy/codex-pdf               DONE              prompt: no
  printwithsynergy/codex-pdf-marketing     FLAG_FLIP         prompt: yes

Wave 2 — Loupe (empty — NO_OP)
  printwithsynergy/loupe-pdf               NO_OP             prompt: no
  printwithsynergy/loupe-pdf-marketing     NO_OP             prompt: no

Wave 3 — Consumers (gated on Wave 1 only; loupe collapsed)
  printwithsynergy/lint-pdf                INTEGRATE         prompt: yes
  printwithsynergy/compile-pdf             FLAG_FLIP         prompt: yes
  printwithsynergy/lint-pdf-marketing      FLAG_FLIP         prompt: yes
  printwithsynergy/compile-pdf-marketing   FLAG_FLIP         prompt: yes
```

---

### Wave 1 — codex marketing

#### Prompt for `printwithsynergy/codex-pdf-marketing`

```
PRECONDITION: codex-pdf@1.9.0 is published (PyPI + npm `latest`).
Verify with `npm view @printwithsynergy/codex-client@1.9.0`
before opening a PR.

Branch: bump-codex-client-1.9.0

What changed upstream
codex-pdf@1.9.0 completes the unified extraction campaign. The TS
client gained tenant scoping (X-Codex-Tenant header), Retry-After
awareness on 429, and three new endpoint methods
(getTextRegions / computeConformance / listRenders). Surface
additive — existing call sites continue to work.

Files to touch
- package.json: bump `@printwithsynergy/codex-client` from
  `^1.7.0` to `^1.9.0` (or `1.9.0` if you pin exact).
- pnpm-lock.yaml: regenerate via `pnpm install`.
- scripts/smoke-codex-extract.mjs: run it locally and confirm it
  still passes against the deployed codex-pdf-lint-sidecar.
  No code change expected — additive surface.
- src/lib/oss-projects.ts / src/pages/projects.astro: update any
  codex version-string references (search for "1.7.0" or
  "1.8.x").
- src/pages/docs/index.astro: if a "what's new" callout is in
  rotation, add 1.9.0 (tenancy, rate limit, observability,
  policies). docs/unified-extraction.md + docs/policies.md +
  docs/slos.md in codex-pdf/main are the canonical sources.

Behavior-locking test before the change
Snapshot the rendered `/projects` page. Diff after the bump —
content should be identical except where you explicitly added
1.9.0 callouts.

Rules
- Draft PR only.
- No --no-verify, --no-gpg-sign, --accept-data-loss.
- New commits only; never amend a pushed commit.
```

---

### Wave 2 — loupe (NO_OP)

Loupe-pdf consumes neither `codex-pdf` nor
`@printwithsynergy/codex-client`. Loupe-pdf-marketing's caret pin
(`^1.6.1`) already auto-accepts `1.9.0` on the next
`pnpm install`; the existing surface still works unchanged. **No
prompts emitted.**

---

### Wave 3 — consumers (gated on Wave 1 only)

#### Prompt for `printwithsynergy/lint-pdf`

```
PRECONDITION: codex-pdf@1.9.0 is published. Verify with
`pip index versions codex-pdf` before opening a PR.
(Loupe is NO_OP for codex; no loupe version gate.)

Branch: bump-codex-1.9.0

What changed upstream
codex-pdf@1.9.0 completes the unified extraction campaign.
- New endpoints (Phase 1, real impls in 1.9.0):
  GET /v1/documents/{pdf_hash}/text-regions
  POST /v1/documents/{document_id}/conformance/{profile}
  GET /v1/documents/{pdf_hash}/renders
- Stage telemetry on every response (`stage_durations_ms` +
  `X-Codex-Stage-Durations-Ms` header).
- Tenancy via `X-Codex-Tenant` header (server scopes cache +
  blob store per tenant).
- Rate limit (`429 + Retry-After`) on compute-and-cache POSTs.
- TTL knob: `CODEX_CACHE_TTL_SECONDS`.
- Bundled Python client gained `tenant` ctor kw, the three new
  endpoint methods, and Retry-After-aware retries.

Files to touch (already discovered in lint-pdf)
- pyproject.toml: bump `codex-pdf>=1.4.4` to `codex-pdf>=1.9.0`.
- src/lintpdf/codex_client.py: confirm the HTTP-backed client
  paths now reach the real endpoints (no more 501). Update the
  feature flag default if Phase 1.5 confirmed sync mode on the
  conformance endpoint (it did — p95 ≤ 14 ms).
- src/lintpdf/codex_adapter.py: collapse the noop fallback if
  every code path now has HTTP coverage.
- Tests that import codex_client: add a tenant-scoped test
  alongside the existing ones to lock the new header.

Behavior-locking test before the change
Snapshot test against the existing preflight pipeline for a
representative fixture corpus. The diff after the bump should be
empty for "no codex" mode and structurally identical for "codex
on" mode (the response shape is additive only).

Rules
- Draft PR only.
- No --no-verify, --no-gpg-sign, --accept-data-loss.
- New commits only; never amend a pushed commit.
```

#### Prompt for `printwithsynergy/compile-pdf`

```
PRECONDITION: codex-pdf@1.9.0 is published. Verify with
`pip index versions codex-pdf` before opening a PR.

Branch: bump-codex-1.9.0

What changed upstream
codex-pdf@1.9.0 completes the unified extraction campaign. Schema
moved from 1.0.0 → 1.2.0 (additive). The published surface is
identical to 1.9.0-rc.3 (which compile-pdf can already pull via
the existing `codex-pdf>=1.8.1,<2.0` range, but you should bump
the floor for clarity and to opt into the new surface
explicitly).

Files to touch (already discovered in compile-pdf)
- pyproject.toml: bump `codex-pdf>=1.8.1,<2.0` to
  `codex-pdf>=1.9.0,<2.0`.
- src/compile_pdf/version.py: bump
  `CODEX_DOCUMENT_SCHEMA_VERSION_PIN` to `1.2.0` (was `1.0.0`).
  This is the version compile-pdf advertises in its
  `/v1/contract` response.
- scripts/consume_surface_audit.py: re-run; should pass because
  compile-pdf only reads codex outputs (no producer surface).

Behavior-locking test before the change
The compile-pdf golden test corpus already runs against codex's
extract — snapshot the output, bump, snapshot again, diff. Any
delta is from the additive fields (detected_text_regions,
stage_durations_ms, conformance_verdicts). Pin those into the
expected snapshot or filter them out — don't silently accept.

Rules
- Draft PR only.
- No --no-verify, --no-gpg-sign, --accept-data-loss.
- New commits only; never amend a pushed commit.
```

#### Prompt for `printwithsynergy/lint-pdf-marketing`

```
PRECONDITION: @printwithsynergy/codex-client@1.9.0 is on the
npm `latest` dist-tag. Verify with `npm view
@printwithsynergy/codex-client dist-tags` before opening a PR.

Branch: bump-codex-client-1.9.0

What changed upstream
TS client now ships:
- `tenant` constructor option → `X-Codex-Tenant` header (also
  reads `CODEX_TENANT` env).
- `getTextRegions`, `computeConformance`, `listRenders` methods.
- 429 retries honour `Retry-After`.

Files to touch (already discovered in lint-pdf-marketing)
- package.json: bump `@printwithsynergy/codex-client` from
  `1.8.1` to `1.9.0` (currently an exact pin).
- pnpm-lock.yaml: regenerate via `pnpm install`.
- scripts/smoke-codex-extract.mjs: run locally. No code change
  expected (additive surface), but worth confirming.
- src/lib/codex.ts: if it instantiates `HttpClient`, consider
  passing `tenant` from env (`CODEX_TENANT`) so the marketing
  demo lands in a separate cache slot from the lint-pdf
  production tenant. Optional; default tenant works.
- src/components/DemoExperience.tsx: if the demo shows
  per-stage timings, surface `stage_durations_ms` from the
  extract response.

Behavior-locking test before the change
Smoke `scripts/smoke-codex-extract.mjs` before and after; diff
should be empty save for any explicit new feature usage.

Rules
- Draft PR only.
- No --no-verify, --no-gpg-sign, --accept-data-loss.
- New commits only; never amend a pushed commit.
```

#### Prompt for `printwithsynergy/compile-pdf-marketing`

```
PRECONDITION: @printwithsynergy/codex-client@1.9.0 is on the
npm `latest` dist-tag. Verify with `npm view
@printwithsynergy/codex-client dist-tags` before opening a PR.

Branch: bump-codex-client-1.9.0

What changed upstream
TS client gained tenant support, Retry-After awareness, and three
new endpoint methods. Surface additive — existing call sites
continue to work.

Files to touch (already discovered in compile-pdf-marketing)
- package.json: bump `@printwithsynergy/codex-client` from
  `^1.8.1` to `^1.9.0` (caret already accepts 1.9.0; bumping
  the floor makes the intent explicit).
- pnpm-lock.yaml: regenerate via `pnpm install`.
- CHANGELOG.md / src/content/changelog/*: optional release-note
  entry if the marketing site tracks consumed-version changes.

Behavior-locking test before the change
No app-level test exists for the codex client here; rely on
`pnpm typecheck` + `pnpm build` to confirm the bump compiles.
The codex sidecar's smoke script (if any) should also pass.

Rules
- Draft PR only.
- No --no-verify, --no-gpg-sign, --accept-data-loss.
- New commits only; never amend a pushed commit.
```

---

### Cross-wave consistency check

Per playbook discipline, re-read the Wave 2 prompts before
emitting Wave 3. Wave 2 has no prompts (loupe NO_OP), and no
loupe public API surface changed in this campaign — so Wave 3
needs no loupe-related call-site updates. Confirmed.

### What to do with this output

Paste each prompt into a fresh Claude Code session (or the
designated downstream worker) scoped to the corresponding repo.
Each prompt is self-contained: branch name, files to touch,
behavior-locking test, sandbox rules. Wave 3 prompts can run in
parallel — they share only the upstream codex version.

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

### Phase 4 — 2026-05-12 — PR #23 — merged

**`1.9.0` final cut:** Python `1.9.0`, TS `1.9.0` (lockstep).
Promotes the rc series to the default channels — PyPI default
resolution; npm `latest` dist-tag. Contract surface identical to
`1.9.0-rc.3`.


**Shipped:**
- ``ConformanceProfile`` enum versioning policy formalised in
  ``docs/policies.md``. Forward-compatible by contract: adding a
  profile is a minor bump; removing or renaming is a major bump
  (we have committed to not do this within the 1.x line).
- Cache TTL knob: ``CODEX_CACHE_TTL_SECONDS`` (default 86400 /
  24h) is the single source of truth for derived-artifact
  lifetime on the Redis backend. ``MemoryCache`` stays LRU-only
  by design — process memory is bounded by bytes, not time. A
  garbage env value falls back to the default with a warning so
  service boot can't break on a typo.
- Backpressure model documented (``policies.md``): rate-limit
  ``429`` + ``Retry-After`` is the only shed-response codex
  emits as a deliberate signal; consumers MUST honour
  ``Retry-After``. ``503`` is reserved for hard failures.
  Executor-pool saturation surfaces as request-side timeouts —
  noted as known limitation, distributed (Redis-backed) limiter
  on the roadmap.
- SLOs published in ``docs/slos.md``: availability targets
  (99.5–99.95% by surface), warm/cold p50/p95/p99 latency tables
  per endpoint, recommended alert lanes (slow vs failing),
  cache-hit-rate floors per endpoint, recommended Prometheus
  query templates.

**Deferred:**
- Real distributed rate-limit accounting (Redis token bucket).
  Tracked as a roadmap item; in-process limiter is fine for the
  rc.x window and the immediate post-1.9.0 deploy.
- 503-as-backpressure semantics. Held off intentionally because
  it changes the contract surface — consumers already know how to
  handle 429. Revisit when distributed accounting lands.
- Cold-cache p95 reduction. SLO targets capture today's reality;
  the speculator + edge cache already mitigate most cold-PDF
  scenarios. Further reduction (e.g. background warmups for
  predicted demand) is post-1.9.0 product work.

**Learned:**
- Centralising TTL behind one env knob simplifies the operator
  story (one number to tune instead of two backends to reason
  about), at the cost of MemoryCache ignoring the knob — but
  that's an honest reflection of reality (in-process LRU has
  size, not time, semantics). Document, don't pretend.
- Publishing SLOs as a doc (not a code-level contract) gives
  operators alert recipes without committing the service to
  numbers it can't yet hit. The rc.x series builds against the
  targets; final 1.9.0 ships when the deployed surface meets
  them.

**Decisions owed:** _none_.

## Next Phase — Plan

All planned phases are complete. The playbook's remaining
invocation is **`synthesize`** — scan the eight repos and emit
wave-ordered consumer + marketing-site prompts for downstream
integration work. Synthesis was gated on "impls stabilise"; with
Phase 4 done, that gate is open.

A final `1.9.0` (non-rc) cut should follow once Phase 4 lands and
soaks. The remaining post-1.9.0 work (real distributed rate
limit, generated SDKs in other languages, bulk OpenAPI
`responses=` cleanup for older endpoints) lives outside the
campaign — it's regular product work.

---

# Codex AI Signal Campaign

## North Star

Move AI signal extraction (language, logos, regulatory symbols,
barcodes, document classification, spell candidates, OCR) from
lint-pdf's `AI_*` rule namespace into codex's data-collection layer.
Lint becomes pure policy-over-data; every consumer
(lint / loupe / compile / future) gets the signals for free,
content-addressed and cached forever.

## Why

Two reasons spelled out in the codex/lint service-boundary doc:

1. **Service boundary.** Codex owns extraction + normalized facts +
   detection signals. Lint owns rules / workflow / verdicts. Today
   AI sits on the wrong side of that line — every consumer that
   wants AI signals pays for its own LLM calls. Moving signal
   extraction to codex makes them part of the canonical fact set,
   shared across all consumers.
2. **Cost.** Codex pays once per `(pdf_hash, signal_kind)`. The
   second consumer hits the cache. With 50% repeat traffic on the
   public demo, fleet cost drops by ~half vs every consumer paying
   independently.

## Design Invariants

Carried over from the unified extraction campaign:

- **Consumer-agnostic surface.** No `lint_*` / `loupe_*` /
  `compile_*` naming. The signal payload is the same shape every
  consumer reads.
- **Two request shapes, both first-class.** First-stop:
  `/v1/extract` returns the full set inline. Second-stop:
  `GET /v1/documents/{pdf_hash}/signals/{kind}` for per-resource
  re-fetch.
- **Cache keys are part of the contract.**
  - `language` / `logos` / `symbols` / `barcodes` / `spell`:
    `(pdf_hash, page_index, kind)`.
  - `classification`: `(pdf_hash, "classification")`.
- **Opt-in AI.** Operator gate (`CODEX_AI_ENABLED`); caller gate
  (`X-Codex-Skip-AI`). Default off so deployments don't accidentally
  spend on Claude calls. When AI is requested but unavailable, codex
  emits a structured `CodexWarning` (`ai_disabled` / `ai_skipped` /
  `ai_signals_pending_impl`) so external apps can render an honest
  "AI signals not available" state instead of pretending the data
  was checked.
- **Detection signals, not verdicts.** `detected_language` says
  what the language IS, not whether it's ALLOWED. That's lint's
  job.
- **Additive only.** No removed or renamed fields across the 1.x
  schema line.

## Phase Plan

- [x] Phase 0 — Contract freeze (this PR)
- [ ] Phase 1 — Implementations (Claude-backed extractors)
- [ ] Phase 1.5 — Cost + latency check; sync-vs-async decision per
      signal kind
- [ ] Phase 2 — Operational contract (tenancy isolation for AI
      cache, per-tenant AI entitlements, rate-limit dimension for
      AI compute)
- [ ] Phase 3 — Consumer rollout (lint migrates `AI_*` rules to
      signal readers; loupe surfaces language / logo badges;
      compile gates producers on detected dielines)
- [ ] Phase 4 — Long-tail (model versioning policy, prompt-version
      header, cost-cap evictions, NSFW / specialised lanes)

## Phase 0 — Contract freeze (this PR)

**Shipped:**

- New model classes: `CodexDetectedLanguage`,
  `CodexDetectedLogo`, `CodexDetectedSymbol`,
  `CodexDetectedBarcode`. Plus `SignalKind` literal type.
- `CodexPage` gains `detected_language`, `detected_logos`,
  `detected_symbols`, `detected_barcodes`, `spell_candidates`.
- `CodexDocument` gains `document_classification: dict[str, float]`.
- Schema bump `1.2.0` → `1.3.0` (additive).
- New stub endpoint `GET /v1/documents/{pdf_hash}/signals/{kind}`
  (501 Not Implemented; contract published).
- New env: `CODEX_AI_ENABLED` (default `false`).
- New header: `X-Codex-Skip-AI: true|false`.
- New `CodexWarning` codes: `ai_disabled`, `ai_skipped`,
  `ai_signals_pending_impl`. Emitted on every `/v1/extract`
  response so consumers can branch UI on the AI state.
- 4 new child JSON schemas under `schemas/v1/`. Top-level schema
  regenerated.

**Deferred:**

- Actual Claude-backed extractors (Phase 1).
- Tenancy isolation knobs specific to AI cache (Phase 2).
- Cost cap + circuit breaker patterns from lint-pdf's
  `ai/legend_claude.py` → codex (Phase 1).

**Decisions owed:**

- Model split per signal kind: Haiku (cheap) vs Sonnet (vision
  quality). Lint-pdf's pattern: Haiku for OCR, Sonnet for swatch
  classification. Codex will mostly mirror this — language /
  spell / classification on Haiku; logos / symbols on Sonnet.
  Defer the final routing until Phase 1 latency numbers are in.

## Next Phase — Plan (for `next` invocation)

**Phase 1 — Implementations.**

- Per signal kind, a thin Claude-backed extractor in
  `codex_pdf.ai/`:
  - `language.py` — `detect_language(page_image)` → BCP-47 + confidence.
  - `logos.py` — `detect_logos(page_image)` → list of bbox + identity.
  - `symbols.py` — `detect_symbols(page_image)` → list of bbox +
    kind from a curated catalogue (GHS, FDA, CE, recycle, …).
  - `barcodes.py` — `decode_barcodes(page_image)` via pyzbar /
    pylibdmtx (specialised, not Claude). Same interface.
  - `classification.py` — `classify_document(pdf_bytes)` →
    `{category: prob}`.
  - `spell.py` — `flag_spell_candidates(text)` → list of unknown
    words, no dictionary policy.
- Same cost-cap + outage-recording pattern as lint-pdf's
  `ai/legend_claude.py`. Aggressive 1h prompt caching.
- Each extractor cached at codex's standard
  `(tenant, pdf_hash, kind)` key. Second reader hits cache.
- Instrument latency per kind. Numbers feed Phase 1.5.

