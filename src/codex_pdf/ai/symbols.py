"""Page-level regulatory / packaging symbol detection (Claude vision)."""

from __future__ import annotations

import base64
import logging

from codex_pdf.ai.claude import call_vision_model, parse_json_payload
from codex_pdf.ai.context import AiContext
from codex_pdf.models.v1 import CodexBBox, CodexDetectedSymbol

logger = logging.getLogger(__name__)

SIGNAL_KIND = "symbols"
SOURCE = "codex-ai/claude-sonnet-4-6"

# Stable identifier catalogue. The list is intentionally not exhaustive —
# consumers must treat unknown ``kind`` values as opaque so codex can
# grow this list additively.
_KNOWN_KINDS = [
    "ghs_flammable",
    "ghs_corrosive",
    "ghs_toxic",
    "ghs_oxidising",
    "ghs_explosive",
    "ghs_health_hazard",
    "ghs_environmental_hazard",
    "ghs_compressed_gas",
    "ghs_acute_toxicity",
    "recycle_pet",
    "recycle_hdpe",
    "recycle_pvc",
    "recycle_ldpe",
    "recycle_pp",
    "recycle_ps",
    "recycle_other",
    "recycle_mobius",
    "fda_drug_facts",
    "fda_nutrition_facts",
    "fda_supplement_facts",
    "ce_marking",
    "ukca_marking",
    "fsc_certified",
    "rainforest_alliance",
    "energy_star",
    "ul_listed",
    "fcc_compliance",
    "rohs_compliant",
    "weee_crossed_bin",
    "tidyman",
    "trademark",
    "registered_trademark",
    "copyright",
]

_SYSTEM = (
    "You are a packaging compliance specialist examining a page from a "
    "print-ready PDF. Identify visible regulatory, safety, sustainability, "
    "or packaging symbols — NOT generic icons, decorative shapes, or "
    "brand logos. Use the canonical kind identifiers from this catalogue "
    "when one matches: " + ", ".join(_KNOWN_KINDS) + ". For symbols not "
    "in the catalogue, use a stable lower-snake-case identifier. For "
    "each symbol, return a tight bounding box in normalised page "
    "coordinates where (0,0) is top-left. Output ONLY valid JSON: "
    '{"symbols": [{"kind": "<id>", "bbox": {"x": <0..1>, "y": <0..1>, '
    '"w": <0..1>, "h": <0..1>}, "confidence": <0..1>}]}. '
    "Empty list when none visible. Do not invent."
)


def extract_symbols(
    *,
    context: AiContext,
    page_png: bytes,
    page_width_pt: float,
    page_height_pt: float,
) -> list[CodexDetectedSymbol]:
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
            "Identify all regulatory / safety / sustainability / packaging "
            "symbols visible on this page. Return JSON as instructed."
        ),
        images_b64=[("image/png", b64)],
        max_tokens=1024,
    )
    if not response:
        return []
    parsed = parse_json_payload(response)
    if not isinstance(parsed, dict):
        logger.warning("symbols extractor: non-dict response: %r", response[:200])
        return []
    items = parsed.get("symbols")
    if not isinstance(items, list):
        return []
    out: list[CodexDetectedSymbol] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        bbox = entry.get("bbox")
        if not isinstance(kind, str) or not kind:
            continue
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
            CodexDetectedSymbol(
                bbox=CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                kind=kind,
                confidence=confidence,
                source=SOURCE,
            )
        )
    return out
