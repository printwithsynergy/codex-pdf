"""Detected text region extraction.

Walks one page with PyMuPDF and emits axis-aligned bounding boxes
in PDF user-space points for every text block PyMuPDF identifies.
``dpi`` is part of the cache key (not the geometry — output is always
in points) so consumers can request a different sampling fidelity
later if a tighter detector lands without invalidating points-based
caches.

Cache key: ``(pdf_hash, page_index, dpi)``.
"""

from __future__ import annotations

from typing import Any

import fitz  # type: ignore[import-untyped]

from codex_pdf.models.v1 import CodexBBox, CodexDetectedTextRegion


def extract_text_regions_for_page(
    pdf_bytes: bytes,
    page_index: int,
    dpi: int = 150,
) -> list[CodexDetectedTextRegion]:
    """Detect text regions on one page.

    Parameters
    ----------
    pdf_bytes:
        Raw PDF bytes.
    page_index:
        Zero-based page index (matches the public endpoint contract).
        Out-of-range indices return an empty list rather than raising
        so callers can ask "is there text on page N?" cheaply.
    dpi:
        Currently unused for geometry (output is in PDF user-space
        points). Carried so the cache key stays
        ``(pdf_hash, page_index, dpi)`` even when a tighter
        DPI-sensitive detector lands later.
    """
    if page_index < 0:
        return []
    regions: list[CodexDetectedTextRegion] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if page_index >= doc.page_count:
            return []
        page = doc.load_page(page_index)
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                # type 0 = text block; type 1 = image block.
                continue
            bbox = block.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x0, y0, x1, y1 = (float(v) for v in bbox)
            text = _block_text(block)
            regions.append(
                CodexDetectedTextRegion(
                    bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                    text=text,
                    confidence=1.0,
                    polygon=[],
                    source="pymupdf",
                )
            )
    return regions


def populate_detected_text_regions(
    pdf_bytes: bytes,
    pages: list,
    dpi: int = 150,
) -> None:
    """Fill ``detected_text_regions`` on each ``CodexPage`` in place.

    Used during full extraction so consumers receive the regions
    in the first-stop response without an extra round trip. Failures
    on individual pages leave that page's list empty rather than
    aborting the whole extraction.
    """
    for page in pages:
        try:
            page.detected_text_regions = extract_text_regions_for_page(
                pdf_bytes, page.page_num - 1, dpi=dpi
            )
        except Exception:
            page.detected_text_regions = []


def _block_text(block: dict[str, Any]) -> str:
    """Concatenate every span in every line of a PyMuPDF text block."""
    parts: list[str] = []
    for line in block.get("lines", []):
        line_parts: list[str] = []
        for span in line.get("spans", []):
            text = span.get("text")
            if isinstance(text, str) and text:
                line_parts.append(text)
        if line_parts:
            parts.append("".join(line_parts))
    return "\n".join(parts)
