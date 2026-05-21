"""Canonical dieline detector shared by summary and consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from codex_pdf.models.v1 import (
    CodexDocument,
    CodexSummaryDielineCandidate,
    CodexSummaryDielineMetrics,
    CodexSummaryDielineSizeMetrics,
)

_DIELINE_PATTERN = re.compile(
    r"(dieline|die ?line|cut ?line|kiss ?cut|crease|fold|trim|perf|knife|cutter)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DielineCalibration:
    """Thresholds for structural dieline detection."""

    fold_dash_ops_min: int = 2
    fold_stroke_ops_min: int = 4
    dieline_stroke_ops_min: int = 8
    dieline_path_ops_min: int = 24
    thin_stroke_ops_min: int = 2
    max_fill_to_stroke_ratio: float = 0.25


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
    calibration: DielineCalibration,
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

    fold_like = (
        dash_ops >= calibration.fold_dash_ops_min
        and stroke_ops >= calibration.fold_stroke_ops_min
    )
    allowed_fill = max(1, int(stroke_ops * calibration.max_fill_to_stroke_ratio))
    dieline_like = (
        stroke_ops >= calibration.dieline_stroke_ops_min
        and path_ops >= calibration.dieline_path_ops_min
        and fill_ops <= allowed_fill
        and (
            thin_stroke_ops >= calibration.thin_stroke_ops_min
            or dash_ops >= calibration.fold_dash_ops_min - 1
        )
    )

    if fold_like:
        fold_reasons: list[str] = []
        fold_confidence = 0.45
        if dash_ops >= calibration.fold_dash_ops_min:
            fold_reasons.append("analysis_dash_pattern")
            fold_confidence += 0.2
        if stroke_ops >= calibration.fold_stroke_ops_min:
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
        if path_ops >= calibration.dieline_path_ops_min:
            dieline_reasons.append("analysis_dense_path_network")
            dieline_confidence += 0.2
        if stroke_ops >= calibration.dieline_stroke_ops_min:
            dieline_reasons.append("analysis_stroke_dominant")
            dieline_confidence += 0.15
        if fill_ops <= allowed_fill:
            dieline_reasons.append("analysis_low_fill_ratio")
            dieline_confidence += 0.1
        if thin_stroke_ops >= calibration.thin_stroke_ops_min:
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


def _page_targets_from_candidates(
    candidates: list[CodexSummaryDielineCandidate],
) -> set[int]:
    pages: set[int] = set()
    page_pattern = re.compile(r"\(page\s+(\d+)", re.IGNORECASE)
    for candidate in candidates:
        match = page_pattern.search(candidate.name)
        if match:
            pages.add(int(match.group(1)))
    return pages


def _append_point(
    points: list[tuple[float, float]],
    x: Any,
    y: Any,
) -> None:
    xf = _as_float(x)
    yf = _as_float(y)
    if xf is None or yf is None:
        return
    points.append((xf, yf))


def _stroke_bboxes_for_page(page_signal: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    content_ops = page_signal.get("content_ops")
    if not isinstance(content_ops, list):
        return []

    current_points: list[tuple[float, float]] = []
    subpath_bboxes: list[tuple[float, float, float, float]] = []
    out: list[tuple[float, float, float, float]] = []

    def _seal_subpath() -> None:
        nonlocal current_points
        if not current_points:
            return
        xs = [p[0] for p in current_points]
        ys = [p[1] for p in current_points]
        subpath_bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        current_points = []

    def _paint_stroke() -> None:
        nonlocal subpath_bboxes
        _seal_subpath()
        if subpath_bboxes:
            out.extend(subpath_bboxes)
            subpath_bboxes = []

    for entry in content_ops:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op") or "").strip()
        operands = entry.get("operands")
        operands_list = operands if isinstance(operands, list) else []

        if op == "m" and len(operands_list) >= 2:
            _seal_subpath()
            _append_point(current_points, operands_list[0], operands_list[1])
        elif op == "l" and len(operands_list) >= 2:
            _append_point(current_points, operands_list[0], operands_list[1])
        elif op in {"c", "v", "y"} and len(operands_list) >= 2:
            _append_point(current_points, operands_list[-2], operands_list[-1])
        elif op == "re" and len(operands_list) >= 4:
            _seal_subpath()
            x = _as_float(operands_list[0])
            y = _as_float(operands_list[1])
            w = _as_float(operands_list[2])
            h = _as_float(operands_list[3])
            if x is not None and y is not None and w is not None and h is not None:
                subpath_bboxes.append((x, y, x + w, y + h))
        elif op in {"S", "s", "B", "B*", "b", "b*"}:
            _paint_stroke()
        elif op in {"f", "f*", "F", "n"}:
            current_points = []
            subpath_bboxes = []

    return out


def _estimate_dieline_size(
    doc: CodexDocument,
    candidates: list[CodexSummaryDielineCandidate],
    *,
    overall_confidence: float,
) -> CodexSummaryDielineSizeMetrics:
    geometry_fallback_confidence = 0.35
    page_targets = _page_targets_from_candidates(candidates)
    all_signals = _iter_page_signals(doc.analysis)
    if not all_signals:
        return CodexSummaryDielineSizeMetrics(
            available=False,
            source="unavailable",
            confidence=0.0,
            provenance=["analysis.page_* missing"],
        )

    selected = [
        (page_num, signal)
        for page_num, signal in all_signals
        if not page_targets or page_num in page_targets
    ]
    bboxes: list[tuple[float, float, float, float]] = []
    for _, signal in selected:
        bboxes.extend(_stroke_bboxes_for_page(signal))
    if not bboxes:
        return CodexSummaryDielineSizeMetrics(
            available=False,
            source="unavailable",
            confidence=0.0,
            provenance=["no stroked path geometry for dieline candidates"],
        )

    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    width_pt = max(0.0, x1 - x0)
    height_pt = max(0.0, y1 - y0)
    size_available = width_pt > 0 or height_pt > 0
    if size_available:
        if overall_confidence > 0.0:
            size_confidence = max(0.0, min(1.0, overall_confidence))
            confidence_basis = "candidate_overall_confidence"
        else:
            # Keep confidence non-zero when geometry itself produced dimensions.
            size_confidence = geometry_fallback_confidence
            confidence_basis = "geometry_fallback_no_candidate_signal"
    else:
        size_confidence = 0.0
        confidence_basis = "unavailable"
    return CodexSummaryDielineSizeMetrics(
        available=size_available,
        x0_pt=round(x0, 3) if size_available else None,
        y0_pt=round(y0, 3) if size_available else None,
        width_pt=round(width_pt, 3),
        height_pt=round(height_pt, 3),
        width_mm=round(width_pt * 25.4 / 72.0, 3),
        height_mm=round(height_pt * 25.4 / 72.0, 3),
        width_in=round(width_pt / 72.0, 4),
        height_in=round(height_pt / 72.0, 4),
        depth_pt=None,
        depth_mm=None,
        depth_in=None,
        depth_available=False,
        depth_note="Unavailable from 2D PDF geometry",
        source="analysis_stroke_bbox",
        confidence=size_confidence,
        provenance=[
            f"pages={','.join(str(page) for page, _ in selected)}",
            "derived_from=analysis.content_ops stroked-path union",
            f"confidence_basis={confidence_basis}",
        ],
    )


def detect_dieline(
    doc: CodexDocument,
    *,
    calibration: DielineCalibration | None = None,
    detector_version: str = "canonical-v1",
) -> CodexSummaryDielineMetrics:
    """Run canonical dieline detection and return analyzer-grade evidence."""

    candidates: list[CodexSummaryDielineCandidate] = []
    seen: set[tuple[str, str, str | None]] = set()
    calibration = calibration or DielineCalibration()

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
        signal_hits = _dieline_signal_candidates(page_num, page_signal, calibration)
        for hit_name, hit_confidence, hit_reasons in signal_hits:
            _add(
                name=hit_name,
                source="analysis_signal",
                confidence=hit_confidence,
                reason_codes=hit_reasons,
            )

    overall_confidence = max((c.confidence for c in candidates), default=0.0)
    size = _estimate_dieline_size(doc, candidates, overall_confidence=overall_confidence)

    # When no named candidate hit any of the registry paths but the
    # geometry-only fallback in ``_estimate_dieline_size`` still
    # produced a size (the bbox-based ``analysis_stroke_bbox``
    # source), synthesise a placeholder candidate so ``count`` and
    # ``candidates`` reflect the detection. Without this consumers
    # see ``Detected dieline size 4.98 x 6.53 in`` alongside
    # ``Dieline candidates: 0`` — confusing nonsense.
    if not candidates and size.available and size.source == "analysis_stroke_bbox":
        candidates = [
            CodexSummaryDielineCandidate(
                name="dieline (bbox)",
                source="analysis_stroke_bbox",
                confidence=size.confidence,
                reason_codes=["geometry_fallback_size_detected"],
            )
        ]
        overall_confidence = size.confidence

    return CodexSummaryDielineMetrics(
        count=len(candidates),
        candidates=candidates,
        overall_confidence=overall_confidence,
        trapped_flag=doc.trapped_flag,
        detector_version=detector_version,
        size=size,
    )
