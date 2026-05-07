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
- decision: Approved (already done in prior cycle)
- date: 2026-05-06T00:00:00Z (publish), 2026-05-07T15:08:00Z (audit confirmation)
- link: https://pypi.org/project/codex-pdf/1.0.0/
- notes: ACCEPTANCE-AUDIT.md row 1 read PyPI as `0.1.1` at audit time; PyPI subsequently reflected `1.0.0` per commit `1e4487b`. Verified via PyPI JSON API immediately before this audit's 1.1.0 cut.

### codex-pdf 1.1.0 publish on PyPI (this audit)
- gate: codex-pdf 1.1.0 publish on PyPI
- decision: Approved (executed)
- date: 2026-05-07T15:36:00Z
- source: Quincy authorization in Multi-Agent Cutover Prompt — cross-cutting actions clause
- evidence: tag `v1.1.0` (commit 794c6f8); https://pypi.org/project/codex-pdf/1.1.0/
- notes: Additive minor bump (`CodexSpotColorant.{lab,cmyk,rgb,pantone_name}` + `CodexDocument.analysis` parser-surface signals). 22/22 tests pass. Built with `uv build` and published via `uv publish` with PYPI_TOKEN.
