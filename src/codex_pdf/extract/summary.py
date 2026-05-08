"""Deterministic document summary derived from CodexDocument fields."""

from __future__ import annotations

import re
from typing import Any

from codex_pdf.models.v1 import (
    CodexDocument,
    CodexDocumentSummary,
    CodexSummaryCountMetrics,
    CodexSummaryDielineCandidate,
    CodexSummaryDielineMetrics,
    CodexSummaryImageMetrics,
    CodexSummaryPageMetrics,
    CodexSummaryPageSize,
    CodexSummarySourceMetrics,
    CodexSummarySpotColor,
    CodexSummarySpotColorMetrics,
)

_DIELINE_PATTERN = re.compile(
    r"(dieline|die ?line|cut ?line|kiss ?cut|crease|fold|trim|perf|knife|cutter)",
    re.IGNORECASE,
)


def _normalize_color_component(value: float) -> float:
    if value <= 1.0:
        return max(0.0, min(1.0, value))
    return max(0.0, min(1.0, value / 100.0))


def _to_u8(component: float) -> int:
    return int(round(max(0.0, min(1.0, component)) * 255))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _fallback_hex(name: str) -> str:
    # Stable name-derived swatches keep demo rendering deterministic.
    acc = 2166136261
    for byte in name.encode("utf-8", "ignore"):
        acc ^= byte
        acc = (acc * 16777619) & 0xFFFFFFFF
    r = 55 + (acc & 0x7F)
    g = 55 + ((acc >> 8) & 0x7F)
    b = 55 + ((acc >> 16) & 0x7F)
    return f"#{r:02x}{g:02x}{b:02x}"


def _spot_colors(doc: CodexDocument) -> CodexSummarySpotColorMetrics:
    by_name: dict[str, CodexSummarySpotColor] = {}
    for cs in doc.color_spaces:
        for colorant in cs.spot_colorants:
            name = colorant.name.strip()
            if not name:
                continue
            if name in by_name:
                continue

            swatch_source = "fallback"
            rgb_u8: tuple[int, int, int] | None = None
            cmyk_norm: tuple[float, float, float, float] | None = None
            swatch_hex = _fallback_hex(name)

            if colorant.rgb is not None:
                rgb_u8 = tuple(_to_u8(_normalize_color_component(v)) for v in colorant.rgb)  # type: ignore[assignment]
                swatch_hex = _rgb_to_hex(rgb_u8)
                swatch_source = "rgb"
            elif colorant.cmyk is not None:
                cmyk_norm = tuple(_normalize_color_component(v) for v in colorant.cmyk)  # type: ignore[assignment]
                c, m, y, k = cmyk_norm
                rgb_u8 = (
                    _to_u8((1 - c) * (1 - k)),
                    _to_u8((1 - m) * (1 - k)),
                    _to_u8((1 - y) * (1 - k)),
                )
                swatch_hex = _rgb_to_hex(rgb_u8)
                swatch_source = "cmyk"

            by_name[name] = CodexSummarySpotColor(
                name=name,
                swatch_hex=swatch_hex,
                swatch_source=swatch_source,  # type: ignore[arg-type]
                rgb=rgb_u8,
                cmyk=cmyk_norm,
                lab=colorant.lab,
                pantone_name=colorant.pantone_name,
            )

    colors = sorted(by_name.values(), key=lambda item: item.name.lower())
    return CodexSummarySpotColorMetrics(count=len(colors), colors=colors)


def _dieline_candidates(doc: CodexDocument) -> CodexSummaryDielineMetrics:
    candidates: list[CodexSummaryDielineCandidate] = []
    seen: set[tuple[str, str, str | None]] = set()

    def _add(
        *,
        name: str,
        source: str,
        ocg_id: str | None = None,
        processing_step: str | None = None,
        confidence: float = 0.5,
        reason_codes: list[str] | None = None,
    ) -> None:
        trimmed = name.strip()
        if not trimmed:
            return
        dedupe_key = (trimmed.lower(), source, ocg_id)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        candidates.append(
            CodexSummaryDielineCandidate(
                name=trimmed,
                source=source,  # type: ignore[arg-type]
                ocg_id=ocg_id,
                processing_step=processing_step,
                confidence=max(0.0, min(1.0, confidence)),
                reason_codes=sorted(set(reason_codes or [])),  # type: ignore[arg-type]
            )
        )

    for ocg in doc.ocgs:
        if _DIELINE_PATTERN.search(ocg.name):
            _add(
                name=ocg.name,
                source="ocg_name",
                ocg_id=ocg.ocg_id,
                confidence=0.95,
                reason_codes=["name_keyword"],
            )
        if ocg.iso19593_processing_step and _DIELINE_PATTERN.search(
            ocg.iso19593_processing_step
        ):
            _add(
                name=ocg.iso19593_processing_step,
                source="ocg_processing_step",
                ocg_id=ocg.ocg_id,
                processing_step=ocg.iso19593_processing_step,
                confidence=0.98,
                reason_codes=["iso19593_processing_step"],
            )

    for layer in doc.trap_evidence.trap_layers:
        layer_name = layer.name or layer.processing_step
        if layer_name and _DIELINE_PATTERN.search(layer_name):
            _add(
                name=layer_name,
                source="trap_layer",
                ocg_id=layer.ocg_id,
                processing_step=layer.processing_step,
                confidence=0.9,
                reason_codes=["trap_layer_keyword"],
            )

    for page_num, page_signal in _iter_page_signals(doc.analysis):
        signal_hits = _dieline_signal_candidates(page_num, page_signal)
        for hit_name, hit_confidence, hit_reasons in signal_hits:
            _add(
                name=hit_name,
                source="analysis_signal",
                confidence=hit_confidence,
                reason_codes=hit_reasons,
            )

    return CodexSummaryDielineMetrics(
        count=len(candidates),
        candidates=candidates,
        overall_confidence=max((c.confidence for c in candidates), default=0.0),
        trapped_flag=doc.trapped_flag,
    )


def _iter_page_signals(analysis: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for key, value in analysis.items():
        if not isinstance(value, dict):
            continue
        if key.startswith("page_"):
            suffix = key.removeprefix("page_")
            if suffix.isdigit():
                out.append((int(suffix), value))
    return sorted(out, key=lambda item: item[0])


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _dieline_signal_candidates(
    page_num: int,
    page_signal: dict[str, Any],
) -> list[tuple[str, float, list[str]]]:
    content_ops = page_signal.get("content_ops")
    if not isinstance(content_ops, list):
        return []

    path_ops = 0
    stroke_ops = 0
    fill_ops = 0
    dash_ops = 0
    thin_stroke_ops = 0
    ocg_marked_content: set[str] = set()

    prop_to_ocg_name = page_signal.get("prop_to_ocg_name")
    ocg_map = prop_to_ocg_name if isinstance(prop_to_ocg_name, dict) else {}

    for entry in content_ops:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op") or "").strip()
        operands = entry.get("operands")
        operands_list = operands if isinstance(operands, list) else []

        if op in {"m", "l", "c", "v", "y", "re", "h"}:
            path_ops += 1
        elif op in {"S", "s"}:
            stroke_ops += 1
        elif op in {"f", "f*", "F", "B", "B*", "b", "b*"}:
            fill_ops += 1
        elif op == "d":
            # Dashed strokes are a strong fold/crease indicator in packaging art.
            if operands_list and isinstance(operands_list[0], list):
                if any((_as_float(x) or 0.0) > 0.0 for x in operands_list[0]):
                    dash_ops += 1
        elif op == "w" and operands_list:
            width = _as_float(operands_list[0])
            if width is not None and width <= 1.0:
                thin_stroke_ops += 1
        elif op == "BDC" and len(operands_list) >= 2:
            maybe_type = str(operands_list[0]).lstrip("/")
            prop_name = str(operands_list[1]).lstrip("/")
            if maybe_type == "OC":
                mapped = ocg_map.get(prop_name)
                if mapped and _DIELINE_PATTERN.search(str(mapped)):
                    ocg_marked_content.add(str(mapped))

    hits: list[tuple[str, float, list[str]]] = []
    for name in sorted(ocg_marked_content):
        hits.append(
            (
                f"{name} (page {page_num}, oc-marked)",
                0.92,
                ["analysis_ocg_marked_keyword"],
            )
        )

    # Structural heuristics for files with missing spot/color semantics.
    fold_like = dash_ops >= 2 and stroke_ops >= 4
    dieline_like = (
        stroke_ops >= 8
        and path_ops >= 24
        and fill_ops <= max(1, stroke_ops // 4)
        and (thin_stroke_ops >= 2 or dash_ops >= 1)
    )

    if fold_like:
        fold_reasons: list[str] = []
        fold_confidence = 0.45
        if dash_ops >= 2:
            fold_reasons.append("analysis_dash_pattern")
            fold_confidence += 0.2
        if stroke_ops >= 4:
            fold_reasons.append("analysis_stroke_dominant")
            fold_confidence += 0.15
        if thin_stroke_ops >= 1:
            fold_reasons.append("analysis_thin_stroke")
            fold_confidence += 0.1
        hits.append(
            (
                f"foldline-like vector strokes (page {page_num})",
                min(0.9, fold_confidence),
                fold_reasons,
            )
        )
    if dieline_like:
        dieline_reasons: list[str] = []
        dieline_confidence = 0.5
        if path_ops >= 24:
            dieline_reasons.append("analysis_dense_path_network")
            dieline_confidence += 0.2
        if stroke_ops >= 8:
            dieline_reasons.append("analysis_stroke_dominant")
            dieline_confidence += 0.15
        if fill_ops <= max(1, stroke_ops // 4):
            dieline_reasons.append("analysis_low_fill_ratio")
            dieline_confidence += 0.1
        if thin_stroke_ops >= 2:
            dieline_reasons.append("analysis_thin_stroke")
            dieline_confidence += 0.05
        if dash_ops >= 1:
            dieline_reasons.append("analysis_dash_pattern")
            dieline_confidence += 0.05
        hits.append(
            (
                f"dieline-like vector path network (page {page_num})",
                min(0.95, dieline_confidence),
                dieline_reasons,
            )
        )
    return hits


def _image_metrics(doc: CodexDocument) -> CodexSummaryImageMetrics:
    dpi_values: list[float] = []
    below_300 = 0
    largest: tuple[int, int, int] | None = None

    for image in doc.images:
        if image.width_px > 0 and image.height_px > 0:
            area = image.width_px * image.height_px
            if largest is None or area > largest[2]:
                largest = (image.width_px, image.height_px, area)

        if image.effective_resolution_dpi is None:
            continue
        avg_dpi = (
            float(image.effective_resolution_dpi.x_dpi)
            + float(image.effective_resolution_dpi.y_dpi)
        ) / 2.0
        dpi_values.append(avg_dpi)
        if avg_dpi < 300:
            below_300 += 1

    if dpi_values:
        dpi_avg = round(sum(dpi_values) / len(dpi_values), 3)
        dpi_min = round(min(dpi_values), 3)
    else:
        dpi_avg = None
        dpi_min = None

    return CodexSummaryImageMetrics(
        dpi_avg=dpi_avg,
        dpi_min=dpi_min,
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
        dieline=_dieline_candidates(doc),
    )
