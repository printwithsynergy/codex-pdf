"""Image extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import CodexBBox, CodexFinding, CodexImage, CodexResolution

_LOW_DPI_ERROR_THRESHOLD = 150
_LOW_DPI_WARN_THRESHOLD = 300


def _effective_dpi_from_placed(
    width_px: int, height_px: int, placed_width_pts: float, placed_height_pts: float
) -> CodexResolution:
    """Compute effective DPI from actual placed dimensions on the page.

    Effective DPI = pixel_dimension / placed_dimension_in_inches.
    A 72px image placed at 2 inches prints at 36 DPI.
    """
    placed_w_in = max(placed_width_pts / 72.0, 0.001)
    placed_h_in = max(placed_height_pts / 72.0, 0.001)
    return CodexResolution(x_dpi=width_px / placed_w_in, y_dpi=height_px / placed_h_in)


def _rect_to_bbox(rect: Any) -> CodexBBox | None:
    try:
        return CodexBBox(
            x0=float(rect.x0),
            y0=float(rect.y0),
            x1=float(rect.x1),
            y1=float(rect.y1),
        )
    except Exception:
        return None


def _stored_dpi_from_xref(doc: Any, xref: int) -> "CodexResolution | None":
    """Extract the DPI stored in the image file header (JPEG JFIF/EXIF, PNG pHYs, etc.).

    PyMuPDF exposes this as ``xres``/``yres`` on the dict returned by
    ``doc.extract_image(xref)``.  Values of 0 or 72 are ambiguous (many
    encoders emit 72 as a default rather than a real measurement) so we
    return ``None`` for those to avoid polluting the average.
    """
    try:
        info = doc.extract_image(xref)
        xres = int(info.get("xres", 0))
        yres = int(info.get("yres", 0))
        # 0 means "not set"; 72 is the de-facto default emitted by many
        # encoders that don't embed real resolution metadata, so treat it
        # as absent.  Values > 72 are genuine stored resolutions.
        if xres > 72 and yres > 72:
            return CodexResolution(x_dpi=float(xres), y_dpi=float(yres))
    except Exception:
        pass
    return None


def collect_low_dpi_findings(images: list[CodexImage]) -> list[CodexFinding]:
    """Emit a CodexFinding for each placed image below the DPI thresholds."""
    findings: list[CodexFinding] = []
    for img in images:
        res = img.effective_resolution_dpi
        if res is None:
            continue
        actual_dpi = min(res.x_dpi, res.y_dpi)
        if actual_dpi >= _LOW_DPI_WARN_THRESHOLD:
            continue
        severity = "error" if actual_dpi < _LOW_DPI_ERROR_THRESHOLD else "warning"
        bbox = None
        if img.bbox_effective is not None:
            b = img.bbox_effective
            bbox = (b.x0, b.y0, b.x1, b.y1)
        stored = img.stored_resolution_dpi
        findings.append(
            CodexFinding(
                id=f"low_dpi-{img.image_id}",
                type="low_dpi",
                severity=severity,
                page=img.page_num,
                bbox=bbox,
                message=f"Image effective resolution {actual_dpi:.0f} DPI is below the {_LOW_DPI_WARN_THRESHOLD} DPI threshold.",
                code=f"LOW_DPI_{actual_dpi:.0f}",
                data={
                    "actual_dpi": round(actual_dpi, 1),
                    "stored_dpi": round(min(stored.x_dpi, stored.y_dpi), 1) if stored else None,
                    "image_id": img.image_id,
                },
            )
        )
    return findings


def extract_images_fitz(doc: Any) -> list[CodexImage]:
    images: list[CodexImage] = []
    for page_num, page in enumerate(doc, start=1):
        try:
            for img in page.get_images(full=True):
                xref = img[0] if len(img) > 0 else -1
                width = int(img[2]) if len(img) > 2 else 0
                height = int(img[3]) if len(img) > 3 else 0
                bpc = int(img[4]) if len(img) > 4 else None
                cs_name = str(img[5]) if len(img) > 5 else None
                filters = str(img[8]) if len(img) > 8 and img[8] is not None else None
                smask = bool(img[1]) if len(img) > 1 else False

                stored_dpi = _stored_dpi_from_xref(doc, xref) if xref > 0 else None

                # Get actual placement rect(s) — an XObject can appear
                # multiple times on the same page at different sizes/positions.
                try:
                    rects = page.get_image_rects(xref) if xref > 0 else []
                except Exception:
                    rects = []

                if rects:
                    for placement_idx, rect in enumerate(rects):
                        placed_w = max(float(getattr(rect, "width", 0.0)), 0.001)
                        placed_h = max(float(getattr(rect, "height", 0.0)), 0.001)
                        images.append(
                            CodexImage(
                                image_id=f"p{page_num}-x{xref}-{placement_idx}",
                                page_num=page_num,
                                width_px=width,
                                height_px=height,
                                bits_per_component=bpc,
                                color_space_id=cs_name,
                                compression=filters,
                                soft_mask=smask,
                                placed_width_pts=placed_w,
                                placed_height_pts=placed_h,
                                bbox_effective=_rect_to_bbox(rect),
                                effective_resolution_dpi=_effective_dpi_from_placed(
                                    width, height, placed_w, placed_h
                                ),
                                stored_resolution_dpi=stored_dpi,
                            )
                        )
                else:
                    # Image defined in resources but no rects found — still emit
                    # the image record without placement-based DPI.
                    images.append(
                        CodexImage(
                            image_id=f"p{page_num}-x{xref}",
                            page_num=page_num,
                            width_px=width,
                            height_px=height,
                            bits_per_component=bpc,
                            color_space_id=cs_name,
                            compression=filters,
                            soft_mask=smask,
                            stored_resolution_dpi=stored_dpi,
                        )
                    )
        except Exception:
            continue
    return images
