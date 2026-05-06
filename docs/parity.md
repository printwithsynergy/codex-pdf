---
title: "Parity"
description: "Projection-based parity checks used to compare codex output with external baselines."
group: "Reference"
order: 5
---

# Parity

Parity verifies that codex output matches an expected projection shape.

## Profiles

- `summary` — core viewer essentials (version, page count, encryption, boxes)
- `inventory` — aggregate and per-page inventory counts
- `deep` — expanded conformance/trapping/count snapshots

## Output

Parity writes a JSON report with:

- profile
- fixture set
- per-file case results
- diff list per case
- total diff count

## Typical workflow

```bash
uv run codex-pdf parity --fixtures-root tests/fixtures --profile summary
uv run codex-pdf parity --fixtures-root tests/fixtures --profile inventory
uv run codex-pdf parity --fixtures-root tests/fixtures --profile deep
```

Use `--fail-on-diff` in CI for gating.
