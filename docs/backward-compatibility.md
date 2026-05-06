---
title: "Backward Compatibility"
description: "Consumer payload compatibility expectations during codexPDF rollout and cutover."
group: "Project"
order: 8
---

# Backward Compatibility Requirements

During migration, preserve the existing consumer payloads while introducing
codex-backed data:

- `lint-pdf` viewer essentials shape:
  - `pdf_version`, `page_count`, `is_encrypted`, `pages`, `info_dict`
- findings payload fields consumed by `lint-pdf-ui`:
  - `inspection_id`, `severity`, `page_num`, `bbox`, `message`, `details`
- `loupe-pdf/types` public contracts:
  - `PageInfo`, `LayerInfo`, `ViewerConfig`, `ColorSample`

Compatibility strategy:

1. Keep existing endpoints unchanged.
2. Introduce codex fields as additive (`codex_*`) metadata.
3. Maintain feature-flagged fallback to legacy parser paths.
