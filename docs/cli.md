---
title: "CLI"
description: "Command reference for extract, schema, validate, probe, and parity workflows."
group: "Getting started"
order: 3
---

# CLI

`codex-pdf` exposes a contract-oriented CLI.

## Commands

- `extract <input_pdf>` — emit full `CodexDocument` JSON.
- `schema` — print schema JSON (published or runtime-generated).
- `validate <codex_json>` — validate output against published schema.
- `probe <input_pdf>` — return lightweight metadata summary.
- `parity` — compare codex projections against baseline projections.

## Common usage

```bash
uv run codex-pdf extract input.pdf --pretty > out.json
uv run codex-pdf validate out.json
uv run codex-pdf probe input.pdf --json
```

## Parity usage

```bash
uv run codex-pdf parity \
  --fixtures-root tests/fixtures \
  --profile deep \
  --max-files 10
```

Baseline command mode:

```bash
uv run codex-pdf parity \
  --fixtures-root /path/to/pdfs \
  --profile summary \
  --baseline-command "<command with {pdf} placeholder>"
```
