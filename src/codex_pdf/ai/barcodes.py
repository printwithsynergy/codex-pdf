"""Page-level barcode detection (pure CPU, no Claude calls).

Uses ``pyzbar`` for 1D + QR codes and ``pylibdmtx`` for DataMatrix.
Both are optional dependencies in the ``[ai]`` extras bag; if either
fails to import the extractor degrades gracefully (empty list).
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from codex_pdf.ai.context import AiContext
from codex_pdf.models.v1 import CodexBBox, CodexDetectedBarcode

logger = logging.getLogger(__name__)

SIGNAL_KIND = "barcodes"
SOURCE_PYZBAR = "codex-cpu/pyzbar"
SOURCE_PYLIBDMTX = "codex-cpu/pylibdmtx"

_PYZBAR_FORMAT_MAP: dict[str, str] = {
    "EAN13": "ean13",
    "EAN8": "ean8",
    "UPCA": "upca",
    "UPCE": "upce",
    "CODE128": "code128",
    "CODE39": "code39",
    "CODE93": "code93",
    "CODABAR": "codabar",
    "ITF": "itf",
    "I25": "i25",
    "PDF417": "pdf417",
    "AZTEC": "aztec",
    "QRCODE": "qr",
}


def _try_pyzbar(png_bytes: bytes, page_height_pt: float, dpi: int) -> list[CodexDetectedBarcode]:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as pyzbar_decode
    except ImportError:
        return []
    try:
        with Image.open(BytesIO(png_bytes)) as img:
            results = pyzbar_decode(img)
    except Exception:
        logger.exception("pyzbar decode failed")
        return []
    out: list[CodexDetectedBarcode] = []
    scale = 72.0 / dpi
    for r in results:
        rect = getattr(r, "rect", None)
        if rect is None:
            continue
        x_px, y_px, w_px, h_px = rect.left, rect.top, rect.width, rect.height
        x0 = x_px * scale
        # Flip Y from pixel-down to PDF-up coordinates.
        y0 = page_height_pt - (y_px + h_px) * scale
        x1 = x0 + w_px * scale
        y1 = y0 + h_px * scale
        format_name = _PYZBAR_FORMAT_MAP.get(getattr(r, "type", "") or "", "")
        if not format_name:
            continue
        try:
            value = r.data.decode("utf-8", errors="replace")
        except Exception:
            value = ""
        out.append(
            CodexDetectedBarcode(
                bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                format=format_name,
                value=value,
                confidence=1.0,
                source=SOURCE_PYZBAR,
            )
        )
    return out


def _try_pylibdmtx(
    png_bytes: bytes, page_height_pt: float, dpi: int
) -> list[CodexDetectedBarcode]:
    try:
        from PIL import Image
        from pylibdmtx.pylibdmtx import decode as dmtx_decode
    except ImportError:
        return []
    try:
        with Image.open(BytesIO(png_bytes)) as img:
            results = dmtx_decode(img)
    except Exception:
        logger.exception("pylibdmtx decode failed")
        return []
    out: list[CodexDetectedBarcode] = []
    scale = 72.0 / dpi
    for r in results:
        rect = getattr(r, "rect", None)
        if rect is None:
            continue
        x0 = rect.left * scale
        y0 = page_height_pt - (rect.top + rect.height) * scale
        x1 = x0 + rect.width * scale
        y1 = y0 + rect.height * scale
        try:
            value = r.data.decode("utf-8", errors="replace")
        except Exception:
            value = ""
        out.append(
            CodexDetectedBarcode(
                bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                format="datamatrix",
                value=value,
                confidence=1.0,
                source=SOURCE_PYLIBDMTX,
            )
        )
    return out


def extract_barcodes(
    *,
    context: AiContext,
    page_png: bytes,
    page_height_pt: float,
    render_dpi: int,
    page_text: str | None = None,
) -> list[CodexDetectedBarcode]:
    """Decode barcodes on a page render.

    ``page_png`` is a rasterisation of the page at ``render_dpi`` DPI
    (codex's standard 150 DPI works for most retail symbologies).
    ``page_height_pt`` is the original PDF page height in points so
    the decoder can translate Y-down pixel coordinates back to
    Y-up PDF user space.

    No Claude call → cost cap is not consulted. The extractor still
    respects the operator/caller gate via ``context.runnable``
    because barcodes are part of the signal contract.
    """
    if not context.runnable:
        return []
    if not page_png:
        return []
    results = _try_pyzbar(page_png, page_height_pt, render_dpi)
    results.extend(_try_pylibdmtx(page_png, page_height_pt, render_dpi))
    return results


def _ignored_text(_text: str | None) -> None:
    """Signal-kind dispatch consistency: barcodes don't read text."""
    return None


# Helper kept on the module for parity with the other extractors.
_ = _ignored_text


def has_decoders_installed() -> bool:
    """True iff at least one decoder library is importable.

    Used by the dispatcher to short-circuit and emit
    ``ai_skipped_no_decoder`` when neither library is available
    rather than running and returning silently empty results.
    """
    try:
        import pyzbar.pyzbar  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import pylibdmtx.pylibdmtx  # noqa: F401
        return True
    except ImportError:
        return False


def _suppress_value(value: Any) -> Any:
    """Future hook for tenant-aware barcode-value redaction.

    Phase 1 returns values verbatim; Phase 2 will consult tenant
    entitlements to redact (for instance, retail UPCs may be
    redacted on a public demo deployment).
    """
    return value
