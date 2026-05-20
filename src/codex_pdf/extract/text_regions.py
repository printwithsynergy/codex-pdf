"""Detected text region extraction.

Walks one page with PyMuPDF and emits axis-aligned bounding boxes
in PDF user-space points for every text block PyMuPDF identifies.
``dpi`` is part of the cache key (not the geometry — output is always
in points) so consumers can request a different sampling fidelity
later if a tighter detector lands without invalidating points-based
caches.

When PyMuPDF finds no selectable text (outlined glyphs, image-only
pages), the extractor falls back to Tesseract OCR: the page is
rendered to a raster image via PyMuPDF and passed to
``pytesseract.image_to_data``. Bboxes are converted from pixel space
back to PDF user-space points. Requires the ``[ai]`` extra (Pillow)
and the ``pytesseract`` package plus the ``tesseract`` system binary;
absent dependencies degrade gracefully to an empty list.

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
        Resolution used when rendering the page for the Tesseract OCR
        fallback path. Also carried in the cache key
        ``(pdf_hash, page_index, dpi)`` so a tighter DPI-sensitive
        detector landing later does not invalidate existing caches.
    """
    if page_index < 0:
        return []
    regions = _extract_pymupdf(pdf_bytes, page_index)
    if regions:
        return regions
    # Fallback: Tesseract OCR for pages with no selectable text
    # (outlined glyphs, scanned / image-only pages).
    return _extract_tesseract(pdf_bytes, page_index, dpi)


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_pymupdf(
    pdf_bytes: bytes,
    page_index: int,
) -> list[CodexDetectedTextRegion]:
    """Extract text regions using PyMuPDF's selectable-text path."""
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


def _extract_tesseract(
    pdf_bytes: bytes,
    page_index: int,
    dpi: int,
) -> list[CodexDetectedTextRegion]:
    """OCR fallback using Tesseract for pages with no selectable text."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return []

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_index >= doc.page_count:
                return []
            page = doc.load_page(page_index)
            page_rect = page.rect  # width/height in PDF points
            page_w_pt = page_rect.width
            page_h_pt = page_rect.height
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_w_px = pix.width
            img_h_px = pix.height
    except Exception:
        return []

    try:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception:
        return []

    regions: list[CodexDetectedTextRegion] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        # Tesseract bboxes: left, top, width, height in pixels (top-left origin)
        left = float(data["left"][i])
        top = float(data["top"][i])
        width = float(data["width"][i])
        height = float(data["height"][i])
        # Convert pixel → PDF points, flip y-axis (PDF origin: bottom-left)
        sx = page_w_pt / img_w_px if img_w_px else 1.0
        sy = page_h_pt / img_h_px if img_h_px else 1.0
        x0 = left * sx
        x1 = (left + width) * sx
        y0 = page_h_pt - (top + height) * sy  # flip
        y1 = page_h_pt - top * sy
        if x1 <= x0:
            x1 = x0 + 0.001
        if y1 <= y0:
            y1 = y0 + 0.001
        regions.append(
            CodexDetectedTextRegion(
                bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                text=text,
                confidence=conf / 100.0,
                polygon=[],
                source="tesseract",
            )
        )
    return regions


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
