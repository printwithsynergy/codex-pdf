# Codex PDF — Agent Guidance

## Service boundary

Codex is the extraction and normalized intelligence layer in the Print with Synergy stack.

- Own extraction, normalization, and reusable summary payloads.
- Keep outputs deterministic, versioned, and backward-compatible.
- Expose detection signals, not policy verdicts.

## Non-goals for this repo

- Do not implement viewer/UI presentation concerns here.
- Do not encode customer policy/rule pass-fail logic here.

Those belong to Loupe (display) and Lint (rules/workflow).

## Offshoot rule

For new products (Forge, Trap, Impose, Marks, etc.), map capabilities to one owner:

1. Display/inspection -> Loupe
2. Rules/reporting/workflow -> Lint
3. Extraction/normalized facts -> Codex

When work spans layers, define a contract seam and keep logic in its owner service.
