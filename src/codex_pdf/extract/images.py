"""Image extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import CodexImage, CodexResolution


def _estimate_dpi(width_px: int, height_px: int, page_width_pts: float, page_height_pts: float) -> CodexResolution:
    width_in = max(page_width_pts / 72.0, 0.001)
    height_in = max(page_height_pts / 72.0, 0.001)
    return CodexResolution(x_dpi=width_px / width_in, y_dpi=height_px / height_in)


def extract_images_fitz(doc: Any) -> list[CodexImage]:
    images: list[CodexImage] = []
    for page_num, page in enumerate(doc, start=1):
        page_w = float(getattr(page.rect, "width", 0.0))
        page_h = float(getattr(page.rect, "height", 0.0))
        try:
            for img in page.get_images(full=True):
                xref = img[0] if len(img) > 0 else -1
                width = int(img[2]) if len(img) > 2 else 0
                height = int(img[3]) if len(img) > 3 else 0
                bpc = int(img[4]) if len(img) > 4 else None
                cs_name = str(img[5]) if len(img) > 5 else None
                filters = str(img[8]) if len(img) > 8 and img[8] is not None else None
                smask = bool(img[1]) if len(img) > 1 else False
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
                        effective_resolution_dpi=_estimate_dpi(width, height, page_w, page_h),
                    )
                )
        except Exception:
            continue
    return images
