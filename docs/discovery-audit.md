---
title: "Discovery Audit"
description: "Initial cross-repo parsing inventory used to design codexPDF migration boundaries and ownership."
group: "Project"
order: 6
---

# Discovery Audit

This audit captures parse-related extraction surfaces before migration.

## lint-pdf

- `src/lintpdf/parser/pikepdf_adapter.py`
  - `PikePDFAdapter.open`, `parse_content_stream`, `get_stream_data`
  - **Disposition:** move/rewrite into codex extractor core.
- `src/lintpdf/semantic/builder.py`
  - `SemanticModelBuilder.build`
  - **Disposition:** rewrite into codex semantic inventory builder.
- `src/lintpdf/semantic/interpreter.py`
  - `ContentStreamInterpreter.interpret`
  - **Disposition:** rewrite with codex event inventory contracts.
- `src/lintpdf/imports/*.py`
  - `PitStopXmlParser`, `Callas*Parser`, `AcrobatXmlParser`
  - **Disposition:** adapt into codex preflight ingest adapters.

## lint-pdf-ui

- `packages/viewer-shared/src/PdfViewer.tsx`
  - `mergeConfig` JSON normalization for viewer payloads.
  - **Disposition:** stay in UI; add codex compatibility fields.
- `packages/viewer-shared/src/lintpdf/sources/finding-overlay.ts`
  - finding-to-overlay translation.
  - **Disposition:** stay in UI boundary adapter.

## loupe-pdf

- `browser/index.ts`
  - `extractOcgIds`, `detectSpotInksFromPdfBytes`
  - **Disposition:** move facts extraction to codex, keep rendering in loupe.
- `fallback-pdfjs/index.ts`
  - fallback page/layer extraction.
  - **Disposition:** keep as visualization fallback path.
- `types/index.ts`
  - core viewer contracts.
  - **Disposition:** extend for codex document transport shape.

## assay-pdf

- `src/assay_pdf/spec/parser.py`
  - GWG workbook parsing.
  - **Disposition:** stay in assay.
- `src/assay_pdf/harness/runners/*.py`
  - engine output parsing.
  - **Disposition:** add codex CLI shell-out runner; keep MIT boundary.

## Overlap highlights

- Duplicate parse facts for OCG/layers across `lint-pdf` and `loupe-pdf`.
- Spot/separation extraction differs in completeness.
- UI contract naming drift risk (`findings_source` vs historical `preflight_source`).
