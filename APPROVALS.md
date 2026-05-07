# codex-pdf — STOP-Gate Approvals (mirror)

Authoritative source: `/Users/macadmin/synergy-agents/approvals.md`. This
file mirrors the entries that affect `codex-pdf` for in-repo
discoverability.

## Entries

### Codex spot-colorant additive Lab/CMYK fields
- gate: Codex spot-colorant additive Lab/CMYK fields
- decision: Approved
- date: 2026-05-07T00:00:00Z
- source: Quincy authorization in Multi-Agent Cutover Prompt + QUESTIONABLE-DECISIONS.md 2026-05-07 codex spot-colorant additive Lab/CMYK fields
- evidence: `src/codex_pdf/models/v1.py`, `schemas/v1/codex-document.schema.json`, `schemas/v1/codex-spot-colorant.schema.json`

### Codex contract changes (additive analysis side-channel)
- gate: Codex contract changes (additive analysis side-channel)
- decision: Approved
- date: 2026-05-07T00:00:00Z
- source: Quincy authorization + QUESTIONABLE-DECISIONS.md 2026-05-07 codex analysis side-channel for dieline parity
- notes: `CodexDocument.analysis` extended additively; downstream lint-pdf migrated analyzers consume the new signals through `lintpdf.codex_adapter`.

### codex-pdf 1.0.0 publish on PyPI
- gate: codex-pdf 1.0.0 publish on PyPI
- decision: Deferred
- date: 2026-05-07T15:08:00Z
- source: Quincy authorization in Multi-Agent Cutover Prompt — release path conditional ("only when validation passes")
- notes: Currently at `0.1.1` on PyPI per ACCEPTANCE-AUDIT.md row 1. No 1.0.0 cut performed in this audit cycle. Release infrastructure (`uv build` + `UV_PUBLISH_TOKEN`) validated and documented; awaiting Quincy explicit go for major version bump.
