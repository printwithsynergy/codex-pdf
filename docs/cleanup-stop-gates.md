---
title: "Cleanup Stop Gates"
description: "Release gates required before downstream parser deletion and hard cutover enforcement."
group: "Project"
order: 9
---

# STOP-Gated Cleanup Policy

No deletions of parse code in downstream repositories are permitted until all
conditions below pass and are approved.

## Required gates

1. Dual-run parity report on reference corpus is green.
2. Contract schema remains backward-compatible for pinned consumers.
3. Latest shipping release candidates of `lint-pdf` and `loupe-pdf` pass CI with codex enabled.
4. Explicit go/no-go approval recorded for each repository cleanup PR.

## Candidate cleanup targets (future)

- `lint-pdf`: direct parser/semantic extraction branches replaced by codex adapter.
- `loupe-pdf`: byte-scan spot extraction where codex provides canonical data.
- `lint-pdf-ui`: legacy field alias handling after all APIs converge.
- `assay-pdf`: none required; codex remains optional shell-out integration.
