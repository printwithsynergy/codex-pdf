# Service Ownership Contract

This contract defines ownership boundaries across the three OSS services:

- `loupe-pdf`: display and visual inspection UX
- `lint-pdf`: reporting, policy/rules, preflight workflow orchestration
- `codex-pdf`: extraction and normalized document intelligence

## Why this exists

We keep implementation reusable and predictable by assigning each concern to one owner.
UIs and new offshoots should consume stable service contracts instead of re-implementing logic.

## Codex ownership (this repo)

Codex owns deterministic extraction and normalized facts:

- PDF structure extraction and source metadata
- reusable `summary` payloads derived from extracted facts
- detection signals that are not policy decisions (for example: candidate dieline layers)
- versioned output contracts and backward-compatible evolution

Codex does **not** own:

- pass/fail policy, customer-specific thresholds, compliance verdicts
- viewer rendering, layout interactions, review UX

## Cross-service boundaries

- Loupe consumes Codex/Lint data and renders it.
- Lint consumes Codex signals and applies policy/rules/workflow semantics.
- Codex remains rule-agnostic so multiple products can reuse it safely.

## Future offshoot rule

New projects (for example: Forge, Trap, Impose, Marks) MUST map each capability to one owner:

1. Display/inspection UX -> Loupe layer
2. Rules/reporting/workflow -> Lint layer
3. Extraction/normalized intelligence -> Codex layer

If a new feature spans layers, split it by contract; do not duplicate core logic across services.
