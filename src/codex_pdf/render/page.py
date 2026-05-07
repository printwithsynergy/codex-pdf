"""Codex page-raster renderer.

Extracted from ``lint-pdf/src/lintpdf/rendering.py``. Produces a PNG
of a single page with optional OCG overrides and overprint
simulation. Ghostscript ``png16m`` honours overprint when the PDF has
spot inks; pdftoppm (poppler) is used as a fallback when Ghostscript
is unavailable, at the cost of overprint fidelity.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile

from codex_pdf.render._common import (
    OCGError,
    apply_ocg_overrides,
    has_ghostscript,
)

logger = logging.getLogger(__name__)


try:
    from pdf2image import convert_from_bytes as _convert_from_bytes

    _HAS_PDF2IMAGE = True
except ImportError:  # pragma: no cover
    _HAS_PDF2IMAGE = False
    _convert_from_bytes = None


def _render_via_ghostscript(pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
    """Render one page with Ghostscript + overprint simulation enabled."""
    with tempfile.TemporaryDirectory(prefix="codex_render_") as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        png_path = os.path.join(tmpdir, "page.png")
        with open(pdf_path, "wb") as fh:
            fh.write(pdf_bytes)

        cmd = [
            "gs",
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=png16m",
            "-sColorConversionStrategy=RGB",
            "-dRenderIntent=0",
            "-dSimulateOverprint=true",
            "-dOverprint=/simulate",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            f"-r{dpi}",
            f"-dFirstPage={page_num}",
            f"-dLastPage={page_num}",
            f"-sOutputFile={png_path}",
            pdf_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Ghostscript render timed out for page {page_num}",
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[:500]
            raise RuntimeError(f"Ghostscript render failed (rc={proc.returncode}): {stderr}")
        if not os.path.exists(png_path):
            raise RuntimeError(f"Ghostscript produced no output for page {page_num}")
        with open(png_path, "rb") as fh:
            return fh.read()


def render_page(
    pdf_bytes: bytes,
    page_num: int,
    *,
    dpi: int = 300,
    ocg_on: list[int] | None = None,
    ocg_off: list[int] | None = None,
    simulate_overprint: bool = True,
) -> bytes:
    """Render a single PDF page to PNG bytes.

    Args:
        pdf_bytes: Raw PDF bytes.
        page_num: 1-indexed page number.
        dpi: Render resolution.
        ocg_on / ocg_off: OCG indices to force visible / hidden. The
            PDF is pre-processed via
            :func:`codex_pdf.render._common.apply_ocg_overrides`.
        simulate_overprint: When True (default), use Ghostscript
            ``-dSimulateOverprint=true`` so spot artwork renders as it
            would print. Falls through to pdftoppm if GS is missing.

    Returns:
        PNG bytes (RGB).

    Raises:
        RuntimeError: No rendering backend available, or GS / pdftoppm
            failed.
        OCGError: ``ocg_on`` / ``ocg_off`` cannot be applied.
    """
    if ocg_on or ocg_off:
        pdf_bytes = apply_ocg_overrides(pdf_bytes, ocg_on, ocg_off)

    if simulate_overprint and has_ghostscript():
        try:
            return _render_via_ghostscript(pdf_bytes, page_num, dpi)
        except RuntimeError:
            logger.exception(
                "Ghostscript render failed; falling back to pdftoppm for page %d",
                page_num,
            )

    if _HAS_PDF2IMAGE:
        images = _convert_from_bytes(
            pdf_bytes,
            first_page=page_num,
            last_page=page_num,
            dpi=dpi,
            fmt="png",
        )
        if not images:
            raise RuntimeError(f"Failed to render page {page_num}")
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue()

    raise RuntimeError("No PDF rendering backend available. Install pdf2image and poppler-utils.")


__all__ = ["OCGError", "render_page"]
