"""Page-level brand / logo detection (Claude vision)."""

from __future__ import annotations

import base64
import logging

from codex_pdf.ai.claude import call_vision_model, parse_json_payload
from codex_pdf.ai.context import AiContext
from codex_pdf.models.v1 import CodexBBox, CodexDetectedLogo

logger = logging.getLogger(__name__)

SIGNAL_KIND = "logos"
SOURCE = "codex-ai/claude-sonnet-4-6"

_SYSTEM = (
    "You are a brand identification specialist examining a page from "
    "a print-ready PDF. Identify visible LOGOS or BRAND MARKS only — "
    "not generic product photography, illustrations, or stock imagery. "
    "For each logo, return its canonical brand name (e.g. 'FedEx', "
    "'USDA Organic', 'CE marking') and a tight bounding box in "
    "normalised page coordinates where (0,0) is top-left and (1,1) "
    "is bottom-right. Output ONLY valid JSON, no prose: "
    '{"logos": [{"identity": "<name>", "bbox": {"x": <0..1>, '
    '"y": <0..1>, "w": <0..1>, "h": <0..1>}, "confidence": <0..1>}]}. '
    "Return an empty list if no logos are visible. Do not invent."
)


def extract_logos(
    *,
    context: AiContext,
    page_png: bytes,
    page_width_pt: float,
    page_height_pt: float,
) -> list[CodexDetectedLogo]:
    """Detect brand logos on a single page render.

    ``page_png`` is the raster used for the call (codex passes its
    pre-existing page render to avoid double rasterisation). The
    extractor converts Claude's normalised coordinates back into PDF
    user-space points using ``page_width_pt`` and ``page_height_pt``.
    """
    if not context.runnable:
        return []
    if not page_png:
        return []
    b64 = base64.b64encode(page_png).decode("ascii")
    response = call_vision_model(
        budget=context.budget,
        signal_kind=SIGNAL_KIND,
        system=_SYSTEM,
        prompt=(
            "Identify all brand logos visible on this page. Return JSON "
            "as instructed."
        ),
        images_b64=[("image/png", b64)],
        max_tokens=1024,
    )
    if not response:
        return []
    parsed = parse_json_payload(response)
    if not isinstance(parsed, dict):
        logger.warning("logos extractor: non-dict response: %r", response[:200])
        return []
    items = parsed.get("logos")
    if not isinstance(items, list):
        return []
    out: list[CodexDetectedLogo] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        identity = entry.get("identity")
        bbox = entry.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            x_n = float(bbox.get("x", 0))
            y_n = float(bbox.get("y", 0))
            w_n = float(bbox.get("w", 0))
            h_n = float(bbox.get("h", 0))
        except (TypeError, ValueError):
            continue
        if w_n <= 0 or h_n <= 0:
            continue
        x0 = max(0.0, min(1.0, x_n)) * page_width_pt
        # Flip Y: Claude emits top-down [0,1]; PDF user-space is bottom-up.
        y_top = max(0.0, min(1.0, y_n)) * page_height_pt
        w_pt = max(0.0, min(1.0, w_n)) * page_width_pt
        h_pt = max(0.0, min(1.0, h_n)) * page_height_pt
        y0 = max(0.0, page_height_pt - y_top - h_pt)
        x1 = x0 + w_pt
        y1 = y0 + h_pt
        try:
            confidence = float(entry.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        confidence = max(0.0, min(1.0, confidence))
        out.append(
            CodexDetectedLogo(
                bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                identity=identity if isinstance(identity, str) and identity else None,
                confidence=confidence,
                source=SOURCE,
            )
        )
    return out
