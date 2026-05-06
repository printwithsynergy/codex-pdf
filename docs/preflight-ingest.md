---
title: "Preflight Ingest"
description: "Adapters that normalize external preflight reports into codex issue payloads."
group: "Reference"
order: 6
---

# Preflight Ingest

codex includes report adapters to normalize external findings into a
single issue model.

## Supported formats

- `lintpdf_json`
- `callas_json`
- `callas_xml`
- `pitstop_xml`
- `acrobat_xml`

## Normalized output

All adapters emit `CodexPreflightReport` with:

- `source_engine`
- `ingest_format`
- normalized `issues` list (`CodexIssue`)
- optional ingest warnings

## Entry point

- Adapter module: `codex_pdf.preflight_ingest.adapters`
- Dispatcher: `parse_preflight_report(content, fmt)`
