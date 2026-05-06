---
title: "Contract and Schemas"
description: "CodexDocument model, schema publishing approach, and compatibility policy."
group: "Reference"
order: 4
---

# Contract and Schemas

The codex public contract is rooted at `CodexDocument`.

## Runtime model

- Python model: `codex_pdf.models.v1.CodexDocument`
- Child types: page boxes, inventories, fonts, images, color spaces, OCGs,
  annotations, preflight reports, and warnings

## Published schemas

- Schema root: `schemas/v1/codex-document.schema.json`
- Child schemas: `schemas/v1/codex-*.schema.json`
- Changelog: `schemas/CHANGELOG.md`

## Versioning policy

- `schema_version` in payload tracks contract version.
- Breaking contract changes increment major version.
- Non-breaking additive changes use minor/patch increments.

## Validation

Use the CLI validator:

```bash
uv run codex-pdf validate out.json
```
