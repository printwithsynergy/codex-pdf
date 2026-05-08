---
trigger: always_on
description: "Service ownership boundary: Codex extraction, Lint rules, Loupe display"
---

# Service Ownership Boundary

- Codex owns extraction + normalized reusable intelligence payloads.
- Lint owns reporting/rules/preflight workflow semantics.
- Loupe owns PDF display and visual inspection UX.
- Keep this repo policy-agnostic; do not add customer-specific pass/fail rule logic.
- New offshoots (Forge, Trap, Impose, Marks, etc.) must map capabilities to one owner layer and integrate via contracts.
