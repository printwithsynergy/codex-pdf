"""Deterministic document summary derived from CodexDocument fields."""

from __future__ import annotations

from codex_pdf.color.resolver import CodexSpotIntent, resolve_spot_swatch_color
from codex_pdf.extract.dieline_detector import detect_dieline
from codex_pdf.models.v1 import (
    CodexDocument,
    CodexDocumentSummary,
    CodexSummaryCountMetrics,
    CodexSummaryImageMetrics,
    CodexSummaryPageMetrics,
    CodexSummaryPageSize,
    CodexSummarySourceMetrics,
    CodexSummarySpotColor,
    CodexSummarySpotColorMetrics,
)

def _normalize_color_component(value: float) -> float:
    if value <= 1.0:
        return max(0.0, min(1.0, value))
    return max(0.0, min(1.0, value / 100.0))


def _to_u8(component: float) -> int:
    return int(round(max(0.0, min(1.0, component)) * 255))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

def _spot_colors(doc: CodexDocument) -> CodexSummarySpotColorMetrics:
    by_name: dict[str, CodexSummarySpotColor] = {}
    color_space_by_id = {space.id: space for space in doc.color_spaces}

    def _analysis_spot_names() -> list[str]:
        analysis = doc.analysis if isinstance(doc.analysis, dict) else {}
        names: list[str] = []
        raw_top = analysis.get("spot_names")
        if isinstance(raw_top, list):
            for raw in raw_top:
                if isinstance(raw, str) and raw.strip():
                    names.append(raw.strip())
        for key, value in analysis.items():
            if not key.startswith("page_") or not isinstance(value, dict):
                continue
            cs_to_spot = value.get("cs_to_spot")
            if not isinstance(cs_to_spot, dict):
                continue
            for raw in cs_to_spot.values():
                if isinstance(raw, str) and raw.strip():
                    names.append(raw.strip())
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            k = name.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(name)
        return out

    def _add_spot(
        *,
        name: str,
        intent: CodexSpotIntent | None,
        prefer_icc_alternate: bool = False,
        icc_note: str | None = None,
    ) -> None:
        key = name.strip().lower()
        if not key or key in by_name:
            return

        resolved = resolve_spot_swatch_color(name, codex_intent=intent)
        rgb_u8 = resolved.rgb
        swatch_hex = _rgb_to_hex(rgb_u8)
        swatch_source = resolved.source
        swatch_note = f"Resolved from {resolved.source}"

        if prefer_icc_alternate and intent is not None and (intent.rgb or intent.cmyk):
            swatch_source = "icc_alternate"
            swatch_note = icc_note or "ICCBased alternate intent"
        elif intent is not None:
            if intent.rgb is not None:
                swatch_source = "rgb"
                swatch_note = "RGB from extractor"
            elif intent.cmyk is not None:
                swatch_source = "cmyk"
                swatch_note = "Projected from CMYK"
            elif intent.lab is not None:
                swatch_source = "lab"
                swatch_note = "Projected from LAB"
            elif intent.pantone_name:
                swatch_source = "pantone"
                swatch_note = f"Pantone {intent.pantone_name}"
            elif resolved.source in {"curated", "hash"}:
                swatch_source = resolved.source
                swatch_note = (
                    "Curated semantic fallback"
                    if resolved.source == "curated"
                    else "Deterministic hash fallback"
                )
            else:
                swatch_source = "fallback"
                swatch_note = "Fallback resolver"
        else:
            if resolved.source in {"pantone", "curated", "hash"}:
                swatch_source = resolved.source
                swatch_note = (
                    "Pantone name match"
                    if resolved.source == "pantone"
                    else "Curated semantic fallback"
                    if resolved.source == "curated"
                    else "Deterministic hash fallback"
                )
            else:
                swatch_source = "fallback"
                swatch_note = "Analysis-only spot fallback"

        by_name[key] = CodexSummarySpotColor(
            name=name,
            swatch_hex=swatch_hex,
            swatch_source=swatch_source,  # type: ignore[arg-type]
            swatch_note=swatch_note,
            rgb=rgb_u8,
            cmyk=intent.cmyk if intent else None,
            lab=intent.lab if intent else None,
            pantone_name=intent.pantone_name if intent else resolved.pantone_name,
        )

    for cs in doc.color_spaces:
        for colorant in cs.spot_colorants:
            name = colorant.name.strip()
            if not name:
                continue
            alt_id = colorant.alternate_space_id or cs.alternate_space_id
            alt_space = color_space_by_id.get(alt_id) if alt_id else None
            normalized_cmyk = (
                tuple(_normalize_color_component(v) for v in colorant.cmyk)
                if colorant.cmyk is not None
                else None
            )
            normalized_rgb = (
                tuple(_to_u8(_normalize_color_component(v)) for v in colorant.rgb)
                if colorant.rgb is not None
                else None
            )
            _add_spot(
                name=name,
                intent=CodexSpotIntent(
                    rgb=normalized_rgb,  # type: ignore[arg-type]
                    cmyk=normalized_cmyk,  # type: ignore[arg-type]
                    lab=colorant.lab,  # type: ignore[arg-type]
                    pantone_name=colorant.pantone_name,
                ),
                prefer_icc_alternate=(
                    cs.family == "ICCBased"
                    or (alt_space is not None and alt_space.family == "ICCBased")
                ),
                icc_note=f"ICCBased alternate via {alt_id or 'unknown'}",
            )

    for analysis_name in _analysis_spot_names():
        _add_spot(name=analysis_name, intent=None)

    colors = sorted(by_name.values(), key=lambda item: item.name.lower())
    return CodexSummarySpotColorMetrics(count=len(colors), colors=colors)


def _image_metrics(doc: CodexDocument) -> CodexSummaryImageMetrics:
    functional_dpi_values: list[float] = []
    actual_dpi_values: list[float] = []
    below_300 = 0
    largest: tuple[int, int, int] | None = None
    seen_xrefs: set[str] = set()

    for image in doc.images:
        if image.width_px > 0 and image.height_px > 0:
            area = image.width_px * image.height_px
            if largest is None or area > largest[2]:
                largest = (image.width_px, image.height_px, area)

        if image.effective_resolution_dpi is not None:
            avg_dpi = (
                float(image.effective_resolution_dpi.x_dpi)
                + float(image.effective_resolution_dpi.y_dpi)
            ) / 2.0
            functional_dpi_values.append(avg_dpi)
            if avg_dpi < 300:
                below_300 += 1

        # actual_dpi is per-XObject (same stored DPI regardless of placement count),
        # so deduplicate by image_id prefix to avoid skewing the average.
        xref_key = image.image_id.rsplit("-", 1)[0]
        if image.stored_resolution_dpi is not None and xref_key not in seen_xrefs:
            seen_xrefs.add(xref_key)
            actual_avg = (
                float(image.stored_resolution_dpi.x_dpi)
                + float(image.stored_resolution_dpi.y_dpi)
            ) / 2.0
            actual_dpi_values.append(actual_avg)

    if functional_dpi_values:
        dpi_avg = round(sum(functional_dpi_values) / len(functional_dpi_values), 3)
        dpi_min = round(min(functional_dpi_values), 3)
    else:
        dpi_avg = None
        dpi_min = None

    if actual_dpi_values:
        actual_dpi_avg = round(sum(actual_dpi_values) / len(actual_dpi_values), 3)
        actual_dpi_min = round(min(actual_dpi_values), 3)
    else:
        actual_dpi_avg = None
        actual_dpi_min = None

    return CodexSummaryImageMetrics(
        dpi_avg=dpi_avg,
        dpi_min=dpi_min,
        actual_dpi_avg=actual_dpi_avg,
        actual_dpi_min=actual_dpi_min,
        below_300_dpi=below_300,
        largest_width_px=largest[0] if largest else None,
        largest_height_px=largest[1] if largest else None,
        largest_area_px2=largest[2] if largest else None,
    )


def _first_page_size(doc: CodexDocument) -> CodexSummaryPageSize | None:
    if not doc.pages:
        return None
    media = doc.pages[0].boxes.media
    width_in = max(0.0, (media.x1 - media.x0) / 72.0)
    height_in = max(0.0, (media.y1 - media.y0) / 72.0)
    return CodexSummaryPageSize(
        width_in=round(width_in, 4),
        height_in=round(height_in, 4),
        width_mm=round(width_in * 25.4, 3),
        height_mm=round(height_in * 25.4, 3),
    )


def _total_page_area_sq_in(doc: CodexDocument) -> float:
    total = 0.0
    for page in doc.pages:
        media = page.boxes.media
        width_in = max(0.0, (media.x1 - media.x0) / 72.0)
        height_in = max(0.0, (media.y1 - media.y0) / 72.0)
        total += width_in * height_in
    return total


def _count_fonts(doc: CodexDocument) -> tuple[int, int, int]:
    embedded = 0
    referenced = 0
    with_missing_glyphs = 0
    for font in doc.fonts:
        if font.embedded in {"full", "subset"}:
            embedded += 1
        elif font.embedded == "referenced":
            referenced += 1
        if font.missing_glyphs_detected:
            with_missing_glyphs += 1
    return embedded, referenced, with_missing_glyphs


def build_document_summary(doc: CodexDocument) -> CodexDocumentSummary:
    embedded, referenced, with_missing_glyphs = _count_fonts(doc)
    total_area_sq_in = _total_page_area_sq_in(doc)

    return CodexDocumentSummary(
        version="1.0",
        counts=CodexSummaryCountMetrics(
            pages=len(doc.pages),
            images=len(doc.images),
            fonts=len(doc.fonts),
            embedded_fonts=embedded,
            referenced_fonts=referenced,
            fonts_with_missing_glyphs=with_missing_glyphs,
        ),
        images=_image_metrics(doc),
        pages=CodexSummaryPageMetrics(
            first_page=_first_page_size(doc),
            total_area_sq_in=round(total_area_sq_in, 4),
            total_area_sq_ft=round(total_area_sq_in / 144.0, 4),
            total_area_sq_mm=round(total_area_sq_in * 645.16, 3),
        ),
        source=CodexSummarySourceMetrics(
            size_bytes=doc.source.size_bytes,
            size_mb=(
                round(doc.source.size_bytes / (1024 * 1024), 6)
                if doc.source.size_bytes is not None
                else None
            ),
        ),
        spot_colors=_spot_colors(doc),
        dieline=detect_dieline(doc),
    )
