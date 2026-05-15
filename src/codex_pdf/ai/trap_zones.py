"""Page-level ink-adjacency detection for trap zone inference (Claude vision)."""

from __future__ import annotations

import base64
import logging
from typing import Any

from codex_pdf.ai.claude import call_vision_model, parse_json_payload
from codex_pdf.ai.context import AiContext
from codex_pdf.models.v1 import CodexTrapZoneCandidate

logger = logging.getLogger(__name__)

SIGNAL_KIND = "trap_zones"
SOURCE = "codex-ai/claude-sonnet-4-6"

_SYSTEM = (
    "You are a print production specialist examining a rendered page from "
    "a print-ready PDF. Identify COLOR REGION BOUNDARIES where ink-pair "
    "trapping would be required — places where two distinct colored areas "
    "meet and a misregistration gap would be visible. "
    "For each boundary, describe the dominant ink on each side (use CMYK "
    "shorthand for process inks: 'Cyan', 'Magenta', 'Yellow', 'Black', "
    "'White'; use the actual name for spot inks if visible, e.g. "
    "'PANTONE 485 C'). Trace the boundary as a list of (x, y) points in "
    "PDF user-space coordinates where (0,0) is bottom-left, x increases "
    "rightward, and y increases upward. Use the page dimensions provided. "
    "Classify the boundary type: 'solid-solid', 'text-bg', "
    "'image-image', or 'image-solid'. "
    "Output ONLY valid JSON, no prose: "
    '{"zones": [{"from_ink": "<ink>", "to_ink": "<ink>", '
    '"polygon_pt": [[x,y], ...], "confidence": <0..1>, '
    '"content_type": "<type>"}]}. '
    "Return an empty list if no trap-worthy boundaries are visible. "
    "Do not invent boundaries that are not clearly present."
)


def extract_trap_zones(
    *,
    context: AiContext,
    page_png: bytes,
    page_index: int,
    page_width_pt: float,
    page_height_pt: float,
) -> list[CodexTrapZoneCandidate]:
    """Detect ink-boundary candidates for trap zone generation on a single page.

    ``page_png`` is the pre-existing page raster (codex reuses the render
    already paid for by other vision analyzers). Returns candidates for
    consumption by compile-pdf-trap when ``trap_zones_source="codex_extract"``.
    """
    if not context.runnable:
        return []
    if not page_png:
        return []
    b64 = base64.b64encode(page_png).decode("ascii")
    prompt = (
        f"Identify all ink-boundary trap zones on this page. "
        f"Page dimensions: {page_width_pt:.1f} x {page_height_pt:.1f} PDF points "
        f"(width x height). (0,0) is bottom-left. Return JSON as instructed."
    )
    response = call_vision_model(
        budget=context.budget,
        signal_kind=SIGNAL_KIND,
        system=_SYSTEM,
        prompt=prompt,
        images_b64=[("image/png", b64)],
        max_tokens=2048,
    )
    if not response:
        return []
    parsed = parse_json_payload(response)
    if not isinstance(parsed, dict):
        logger.warning("trap_zones extractor: non-dict response: %r", response[:200])
        return []
    items = parsed.get("zones")
    if not isinstance(items, list):
        return []
    out: list[CodexTrapZoneCandidate] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        from_ink = entry.get("from_ink")
        to_ink = entry.get("to_ink")
        if not isinstance(from_ink, str) or not from_ink:
            continue
        if not isinstance(to_ink, str) or not to_ink:
            continue
        raw_polygon = entry.get("polygon_pt")
        polygon: list[tuple[float, float]] = []
        if isinstance(raw_polygon, list):
            polygon = _parse_polygon(raw_polygon)
        try:
            confidence = float(entry.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        confidence = max(0.0, min(1.0, confidence))
        raw_ct = entry.get("content_type", "solid-solid")
        content_type = raw_ct if raw_ct in {
            "solid-solid", "text-bg", "image-image", "image-solid"
        } else "solid-solid"
        out.append(
            CodexTrapZoneCandidate(
                page_index=page_index,
                polygon_pt=polygon,
                from_ink=from_ink,
                to_ink=to_ink,
                confidence=confidence,
                content_type=content_type,  # type: ignore[arg-type]
                source=SOURCE,
            )
        )
    return out


def _parse_polygon(raw: list[Any]) -> list[tuple[float, float]]:
    """Convert a raw [[x, y], ...] list to typed pairs, silently dropping invalid entries."""
    result: list[tuple[float, float]] = []
    for point in raw:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                result.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    return result
