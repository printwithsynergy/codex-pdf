"""Shared internal helpers for codex render modules.

Kept private (underscore-prefixed module name) so callers always go
through the public render functions in :mod:`codex_pdf.render`.
"""

from __future__ import annotations

import io
import logging
import shutil

logger = logging.getLogger(__name__)


_gs_checked = False
_has_gs = False


def has_ghostscript() -> bool:
    """Return True iff Ghostscript (``gs``) is on PATH.

    Result is cached after the first lookup. Codex render paths require
    Ghostscript for overprint-faithful raster output and ``tiffsep``
    separations; callers translate a False return into a 503-style
    failure rather than silently degrading.
    """
    global _gs_checked, _has_gs
    if _gs_checked:
        return _has_gs
    _gs_checked = True
    _has_gs = shutil.which("gs") is not None
    if not _has_gs:
        logger.warning(
            "Ghostscript ('gs') not on PATH — codex render paths that "
            "require GS will raise. Install ghostscript >= 10 for "
            "overprint-faithful raster output and tiffsep separations.",
        )
    return _has_gs


class OCGError(ValueError):
    """Raised when an OCG override can't be applied.

    Either the PDF has no OCG dictionary, the requested indices are out
    of range, or ``ocg_on``/``ocg_off`` conflict on the same index.
    Surface as 422 Unprocessable Entity in HTTP handlers.
    """


def apply_ocg_overrides(
    pdf_bytes: bytes,
    ocg_on: list[int] | None,
    ocg_off: list[int] | None,
) -> bytes:
    """Rewrite ``/Root/OCProperties/D/OFF`` so renderers honour overrides.

    Indices refer to positions in ``/Root/OCProperties/OCGs`` — the
    same indices that codex extract publishes as ``ocg_index`` on
    layer-bearing artifacts.
    """
    import pikepdf

    on = set(ocg_on or [])
    off = set(ocg_off or [])
    if not on and not off:
        return pdf_bytes

    conflict = on & off
    if conflict:
        raise OCGError(f"ocg_on and ocg_off conflict on indices {sorted(conflict)}")

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        catalog = pdf.Root
        oc_props = catalog.get("/OCProperties")
        if oc_props is None:
            raise OCGError("PDF has no /OCProperties dictionary; cannot toggle layers.")
        ocgs = oc_props.get("/OCGs")
        if ocgs is None:
            raise OCGError("/OCProperties has no /OCGs array.")

        max_idx = len(ocgs) - 1
        for idx in on | off:
            if idx < 0 or idx > max_idx:
                raise OCGError(f"OCG index {idx} out of range (0..{max_idx}).")

        d = oc_props.get("/D")
        if d is None:
            d = pikepdf.Dictionary()
            oc_props["/D"] = d

        existing_off = d.get("/OFF")
        existing_ids: set[int] = set()
        if existing_off is not None:
            ocg_objs = [ocgs[i] for i in range(len(ocgs))]
            for ref in existing_off:
                for i, ocg in enumerate(ocg_objs):
                    if ref.objgen == ocg.objgen:
                        existing_ids.add(i)
                        break

        new_off_ids = (existing_ids | off) - on
        new_off_refs = [ocgs[i] for i in sorted(new_off_ids)]
        d["/OFF"] = pikepdf.Array(new_off_refs)

        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()


def get_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in ``pdf_bytes`` (0 on parse failure)."""
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def get_page_media_box(pdf_bytes: bytes, page_num: int) -> tuple[float, float, float, float]:
    """Return MediaBox for ``page_num`` (1-indexed) or US-Letter fallback."""
    import pikepdf

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[page_num - 1]
        mb = page.get("/MediaBox")
        if mb is None:
            return (0.0, 0.0, 612.0, 792.0)
        vals = [float(v) for v in mb]
        return (vals[0], vals[1], vals[2], vals[3])
