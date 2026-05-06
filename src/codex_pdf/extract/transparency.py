"""Transparency extraction and lazy sampling descriptor."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import (
    CodexKnockoutGroup,
    CodexLazySamplerDescriptor,
    CodexSoftMask,
    CodexTransparencyGroup,
    CodexTransparencyTree,
)


def extract_transparency_fitz(doc: Any) -> list[CodexTransparencyTree]:
    trees: list[CodexTransparencyTree] = []
    for page_num, _page in enumerate(doc, start=1):
        # Current implementation is structural placeholder for on-demand sampling.
        trees.append(
            CodexTransparencyTree(
                groups=[CodexTransparencyGroup(group_id=f"page-{page_num}-group", isolated=False, knockout=False)],
                soft_masks=[CodexSoftMask(mask_id=f"page-{page_num}-smask", subtype=None)],
                knockout_groups=[CodexKnockoutGroup(group_id=f"page-{page_num}-kg", enabled=False)],
                lazy_sampler=CodexLazySamplerDescriptor(
                    mode="on_demand",
                    endpoint=f"/v1/documents/{{document_id}}/pages/{page_num}/sample",
                ),
            )
        )
    return trees
