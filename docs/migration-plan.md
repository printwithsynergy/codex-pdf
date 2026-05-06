---
title: "Migration Plan"
description: "Phased rollout plan for moving PDF fact extraction from downstream engines into codexPDF."
group: "Project"
order: 7
---

# Migration Plan

## Phase 0 (this repo)

1. Publish `CodexDocument` contract and schema.
2. Ship CLI (`extract`, `schema`, `validate`, `probe`).
3. Commit golden output harness.

## Phase 1 (non-destructive adapters)

1. `lint-pdf`: optional codex-backed extraction path behind feature flag.
2. `lint-pdf-ui`: accept codex payload metadata fields while preserving existing config.
3. `loupe-pdf`: add contract type for codex-fed metadata facts.
4. `assay-pdf`: add codex subprocess runner (`codex-pdf extract`) with no in-process import.

## Phase 2 (parity hardening)

1. Run dual-path comparisons on reference corpus.
2. Resolve mismatches in page boxes, OCG inventory, and spot metadata.
3. Freeze schema at `1.0.0` once parity gates pass.

## Phase 3 (STOP-gated cleanup)

- No parser deletion until explicit approval.
- Submit one deletion proposal per repo with before/after API compatibility proof.
