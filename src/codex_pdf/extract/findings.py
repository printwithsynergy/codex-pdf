"""Collect positioned visual findings from an extracted CodexDocument.

Every extractor that produces spatially located results feeds into
``collect_document_findings``, which returns a flat ``list[CodexFinding]``
suitable for the ``CodexDocument.findings`` field.

Adding a new finding type: implement a ``_findings_from_*`` helper and call
it from ``collect_document_findings``. Keep each helper side-effect-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codex_pdf.models.v1 import CodexFinding

if TYPE_CHECKING:
    from codex_pdf.models.v1 import CodexDocument

_LOW_DPI_ERROR_THRESHOLD = 150.0
_LOW_DPI_WARNING_THRESHOLD = 300.0

_MARKUP_SUBTYPES = {
    "Highlight", "Underline", "StrikeOut", "Squiggly",
    "Ink", "FreeText", "Stamp", "Square", "Circle", "Polygon", "PolyLine",
}


def _bbox_from_codex_bbox(b: Any) -> tuple[float, float, float, float] | None:
    if b is None:
        return None
    try:
        return (float(b.x0), float(b.y0), float(b.x1), float(b.y1))
    except Exception:
        return None


def _findings_from_images(doc: "CodexDocument") -> list[CodexFinding]:
    out: list[CodexFinding] = []
    for img in doc.images:
        if img.effective_resolution_dpi is None:
            continue
        avg_dpi = (
            float(img.effective_resolution_dpi.x_dpi)
            + float(img.effective_resolution_dpi.y_dpi)
        ) / 2.0
        if avg_dpi >= _LOW_DPI_WARNING_THRESHOLD:
            continue
        severity = "error" if avg_dpi < _LOW_DPI_ERROR_THRESHOLD else "warning"
        dpi_int = round(avg_dpi)
        bbox = _bbox_from_codex_bbox(img.bbox_effective)
        out.append(
            CodexFinding(
                id=f"low_dpi-{img.image_id}",
                type="low_dpi",
                severity=severity,
                page=img.page_num,
                bbox=bbox,
                message=f"Image at {dpi_int} DPI (below 300 DPI minimum)",
                code=f"LOW_DPI_{dpi_int}",
                data={
                    "actual_dpi": round(avg_dpi, 1),
                    "stored_dpi": (
                        round(
                            (img.stored_resolution_dpi.x_dpi + img.stored_resolution_dpi.y_dpi) / 2.0,
                            1,
                        )
                        if img.stored_resolution_dpi
                        else None
                    ),
                    "image_id": img.image_id,
                },
            )
        )
    return out


def _findings_from_dieline(doc: "CodexDocument") -> list[CodexFinding]:
    if doc.summary is None:
        return []
    size = doc.summary.dieline.size
    if not size.available:
        return []
    if size.width_pt is None or size.height_pt is None:
        return []
    x0 = size.x0_pt or 0.0
    y0 = size.y0_pt or 0.0
    x1 = x0 + (size.width_pt or 0.0)
    y1 = y0 + (size.height_pt or 0.0)
    # Determine page: use the first candidate that specifies a page, else 1.
    page = 1
    for cand in doc.summary.dieline.candidates:
        import re
        m = re.search(r"page[_\s-]?(\d+)", cand.name or "", re.I)
        if m:
            page = int(m.group(1))
            break
    dims = ""
    if size.width_mm and size.height_mm:
        dims = f" ({round(size.width_mm)}×{round(size.height_mm)} mm)"
    return [
        CodexFinding(
            id="dieline",
            type="dieline",
            severity="info",
            page=page,
            bbox=(x0, y0, x1, y1),
            message=f"Dieline layer detected{dims}",
            code="DIELINE_DETECTED",
            data={
                "confidence": doc.summary.dieline.overall_confidence,
                "source": size.source,
                "candidates": [c.name for c in doc.summary.dieline.candidates],
            },
        )
    ]


def _findings_from_annotations(doc: "CodexDocument") -> list[CodexFinding]:
    out: list[CodexFinding] = []
    for ann in doc.annotations:
        if ann.subtype not in _MARKUP_SUBTYPES:
            continue
        bbox = _bbox_from_codex_bbox(ann.rect)
        label = ann.contents or ann.subtype or "Annotation"
        out.append(
            CodexFinding(
                id=f"annotation-{ann.annotation_id}",
                type="annotation",
                severity="advisory",
                page=ann.page_num,
                bbox=bbox,
                message=f"{ann.subtype} annotation: {label[:120]}",
                code="PDF_ANNOTATION",
                data={"subtype": ann.subtype, "contents": ann.contents},
            )
        )
    return out


def _findings_from_ai_signals(doc: "CodexDocument") -> list[CodexFinding]:
    out: list[CodexFinding] = []
    for page in doc.pages:
        pnum = page.page_num
        for logo in page.detected_logos:
            out.append(
                CodexFinding(
                    id=f"logo-p{pnum}-{logo.identity or 'unknown'}",
                    type="logo",
                    severity="info",
                    page=pnum,
                    bbox=_bbox_from_codex_bbox(logo.bbox),
                    message=f"Logo detected: {logo.identity or 'unidentified'}",
                    code="LOGO_DETECTED",
                    data={"identity": logo.identity, "confidence": logo.confidence},
                )
            )
        for sym in page.detected_symbols:
            out.append(
                CodexFinding(
                    id=f"symbol-p{pnum}-{sym.kind}",
                    type="symbol",
                    severity="info",
                    page=pnum,
                    bbox=_bbox_from_codex_bbox(sym.bbox),
                    message=f"Symbol detected: {sym.kind}",
                    code="SYMBOL_DETECTED",
                    data={"kind": sym.kind, "confidence": sym.confidence},
                )
            )
        for bc in page.detected_barcodes:
            out.append(
                CodexFinding(
                    id=f"barcode-p{pnum}-{bc.format}",
                    type="barcode",
                    severity="info",
                    page=pnum,
                    bbox=_bbox_from_codex_bbox(bc.bbox),
                    message=f"Barcode ({bc.format}): {bc.value[:60]}",
                    code="BARCODE_DETECTED",
                    data={"format": bc.format, "value": bc.value, "confidence": bc.confidence},
                )
            )
        for i, tz in enumerate(page.trap_zone_candidates):
            if not tz.polygon_pt:
                continue
            xs = [p[0] for p in tz.polygon_pt]
            ys = [p[1] for p in tz.polygon_pt]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            out.append(
                CodexFinding(
                    id=f"trap_zone-p{pnum}-{i}",
                    type="trap_zone",
                    severity="advisory",
                    page=pnum,
                    bbox=bbox,
                    message=f"Trap zone candidate: {tz.from_ink} → {tz.to_ink} ({tz.content_type})",
                    code="TRAP_ZONE_CANDIDATE",
                    data={
                        "from_ink": tz.from_ink,
                        "to_ink": tz.to_ink,
                        "content_type": tz.content_type,
                        "confidence": tz.confidence,
                    },
                )
            )
    return out


def collect_document_findings(doc: "CodexDocument") -> list[CodexFinding]:
    """Aggregate positioned visual findings from all codex extractors."""
    findings: list[CodexFinding] = []
    findings.extend(_findings_from_images(doc))
    findings.extend(_findings_from_dieline(doc))
    findings.extend(_findings_from_annotations(doc))
    findings.extend(_findings_from_ai_signals(doc))
    return findings
