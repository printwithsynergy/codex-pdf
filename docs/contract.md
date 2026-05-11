# codex-pdf contract

`codex-pdf` is the read-only PDF facts + render service for the
Think Neverland tooling family (lint-pdf, loupe-pdf, the marketing
demos, and the upcoming Forge producers). This document is the
canonical pointer for every contract surface codex exposes, the
versioning policy that governs each section, and the read-only
invariants the producer-surface audit enforces.

## Contract endpoints (HTTP)

The service mounts at the configured base URL (Railway service
domain or custom apex). Auth modes are documented in
[`docs/deploy.md`](./deploy.md); the table below uses the bearer
mode for examples.

| Endpoint | Section | Owner | Notes |
|---|---|---|---|
| `GET /healthz`, `GET /v1/healthz` | meta | render | unauthed liveness; carries `version` and `cache_backend` |
| `GET /v1/version` | meta | render | bare `{version}` |
| `GET /v1/contract` | meta | render | endpoint inventory + `section_schema_versions` |
| `GET /v1/schema/{name}` | document | extract | JSON schemas served from `schemas/v1/<name>.schema.json` |
| `POST /v1/extract`, `POST /extract` | document | extract | multipart PDF or JSON `{url, pdf_sha256}` → CodexDocument |
| `POST /v1/probe` | document | extract | two-event SSE stream: `probe-min` (instant) + `probe-std` (after secondary parse) |
| `POST /v1/extract/stream` | document | extract | SSE stream of `phase-1` + `phase-2` extract events; `?granular=1` adds per-section progress |
| `POST /v1/render/page` | document | render | PNG raster |
| `POST /v1/render/separations` | document | render | tiffsep channel manifest |
| `POST /v1/render/heatmap` | document | render | TAC heatmap PNG + per-run header |
| `POST /v1/render/layer` | document | render | OCG-toggled layer raster |
| `POST /v1/sample/color` | document | render | per-pixel sRGB sample |
| `POST /v1/sample/density` | document | render | per-channel density sample |
| `POST /v1/walk/content-stream` | document | extract | content-stream signals JSON |
| `POST /v1/walk/type4` | document | eval | Type-4 PostScript evaluator |
| `POST /v1/color/resolve` | color | color | host → codex → pantone → curated → hash resolver |
| `POST /v1/color/match-pantone` | color | color | nearest-Pantone search via ΔE2000 |
| `GET /v1/color/inkbook` | color | color | curated + Pantone catalogue manifest |
| `POST /v1/geom/tile` | geom | geom | imposition tile-grid layout |
| `POST /v1/geom/intersect` | geom | geom | polygon Boolean intersection |
| `POST /v1/geom/union` | geom | geom | polygon Boolean union |
| `POST /v1/geom/difference` | geom | geom | polygon Boolean difference |
| `POST /v1/geom/offset` | geom | geom | polygon inset / outset by signed distance |
| `POST /v1/color/neutral-density` | color | color | per-channel neutral density sample |
| `POST /v1/retention/delete` | retention | extract | erase persisted PDF + extract + meta for an `sha256` from R2 (only meaningful when retention is configured — `CLAUDE.md` deployed surface §4) |
| `GET /metrics` | meta | render | Prometheus metrics (when prometheus-client installed) |

## Schema sections + versioning

Each section under codex versions independently of the top-level
`codex-document` schema. The contract endpoint exposes the per-
section versions in a single map so SDK consumers can pin against
exactly the surface they validate.

| Section | Version constant | Current value | Bump policy |
|---|---|---|---|
| document (codex-document) | embedded in `/v1/contract.schema_version` | `1.0.0` | additive bumps remain `1.x`; breaking changes go to `2.0.0` |
| color | `codex_pdf.color.COLOR_SCHEMA_VERSION` | `1.0.0` | bump on any change to `/v1/color/*` request/response shapes |
| geom | `codex_pdf.geom.GEOM_SCHEMA_VERSION` | `1.0.0` | bump on any change to `/v1/geom/*` request/response shapes |

Sample contract response:

```json
{
  "contract_name": "codex-document",
  "schema_version": "1.1.0",
  "package_version": "1.8.1",
  "schema_id": "https://schemas.thinkneverland.com/codex-pdf/v1/codex-document.schema.json",
  "endpoints": ["POST /v1/extract", "POST /v1/probe", "POST /v1/extract/stream", "..."],
  "section_schema_versions": {
    "color": "1.0.0",
    "geom": "1.0.0"
  }
}
```

Every per-section response also carries `schema_version` inline
(e.g. `ColorResolveResponse.schema_version`) so a consumer that
hits the surface without first calling `/v1/contract` still has the
information it needs to pick a validator.

## Read-only invariants

Codex never produces new PDF bytes. The invariant is enforced by
`scripts/produce_surface_audit.py`, which fails CI when:

- `pikepdf.new()` is invoked anywhere.
- Any `Pdf.save(...)` call appears outside the documented
  `apply_ocg_overrides` allowlist (a transient in-memory PDF fed
  straight to Ghostscript with the requested OCG override applied;
  bytes are never returned to a caller).
- A producer package is imported (`pypdf`, `pdfrw`, `reportlab`,
  `fpdf`, `fpdf2`, `pdfkit`, `borb`).
- A Ghostscript invocation passes a PDF-writer device
  (`-sDEVICE=pdfwrite`, `pdfimage8`, `pdfimage24`, `pdfimage32`).
- `mutool {clean,create,merge}`, `qpdf` write modes, or `cpdf` is
  invoked.
- A `b"%PDF-"` literal is concatenated into output (read-only
  sniffs like `raw[:5] == b"%PDF-"` are explicitly allowed).
- `pikepdf` / `pymupdf` / `fitz` is imported outside the
  allowlist (`codex_pdf.extract.*`, `codex_pdf.render.*`,
  `codex_pdf.preflight_ingest.adapters`, `codex_pdf.eval.ps_type4`,
  `codex_pdf.api.{main,url_ingest}`, `codex_pdf.parity`,
  `codex_pdf.cli`).

The audit emits a JSON report (`reports/audit/produce_surface.json`)
on every CI run alongside the parity gate so reviewers can track
status changes commit-by-commit.

## Forge expansion rule

Any future need to write PDF bytes goes into a separate Forge
service (rewrite, marks, impose, trap), never into a consumer.
Codex stays read-only; consumers stay byte-level-clean.
