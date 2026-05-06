"""Content inventory extraction (object-level placeholders)."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import (
    CodexColorUsage,
    CodexGraphicsStateSnapshot,
    CodexPageObject,
)


def extract_page_inventory_fitz(doc: Any) -> dict[int, list[CodexPageObject]]:
    """Return page_num -> content object inventory.

    This approximates object classes from currently exposed fitz APIs and serves
    as codex's canonical object list until deeper content-stream parser stages
    are plugged in.
    """
    inventories: dict[int, list[CodexPageObject]] = {}
    for page_num, page in enumerate(doc, start=1):
        objects: list[CodexPageObject] = []

        # Text spans via textpage dictionary.
        try:
            tdict = page.get_text("dict")
            block_idx = 0
            for block in tdict.get("blocks", []):
                block_idx += 1
                if block.get("type") == 0:
                    objects.append(
                        CodexPageObject(
                            object_id=f"p{page_num}-text-{block_idx}",
                            kind="text",
                            graphics_state=CodexGraphicsStateSnapshot(),
                            color_usage=CodexColorUsage(),
                        )
                    )
                elif block.get("type") == 1:
                    objects.append(
                        CodexPageObject(
                            object_id=f"p{page_num}-raster-{block_idx}",
                            kind="raster",
                            graphics_state=CodexGraphicsStateSnapshot(),
                            color_usage=CodexColorUsage(),
                        )
                    )
        except Exception:
            pass

        # Drawings become vector objects.
        try:
            drawings = page.get_drawings() or []
            for idx, _draw in enumerate(drawings, start=1):
                objects.append(
                    CodexPageObject(
                        object_id=f"p{page_num}-vector-{idx}",
                        kind="vector",
                        graphics_state=CodexGraphicsStateSnapshot(),
                        color_usage=CodexColorUsage(),
                    )
                )
        except Exception:
            pass

        inventories[page_num] = objects
    return inventories
