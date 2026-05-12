"""Document-level classification (Claude Haiku).

Produces a ``dict[str, float]`` mapping classification label to
probability. Labels are open-ended — codex doesn't enforce a
catalogue at the contract level so consumers must treat unknown
labels as opaque and apply their own policies.
"""

from __future__ import annotations

import logging

from codex_pdf.ai.claude import call_text_model, parse_json_payload
from codex_pdf.ai.context import AiContext

logger = logging.getLogger(__name__)

SIGNAL_KIND = "classification"
SOURCE = "codex-ai/claude-haiku-4-5"

# Suggested labels — bias the model toward stable identifiers but do
# not constrain it. Consumers are required to treat unknown labels
# as opaque (see CAMPAIGN.md AI signal forward-compatibility rule).
_SUGGESTED_LABELS = [
    "label",
    "carton",
    "flexible_packaging",
    "shrink_sleeve",
    "blister_pack",
    "folding_carton",
    "corrugated",
    "brochure",
    "datasheet",
    "drug_facts",
    "nutrition_label",
    "menu",
    "business_card",
    "poster",
    "signage",
    "book_cover",
    "magazine",
    "invoice",
    "form",
    "letter",
    "other",
]

_MAX_TEXT_CHARS = 6000

_SYSTEM = (
    "You are a document classification model for print production. "
    "Score the probability the document belongs to each of these "
    "categories: " + ", ".join(_SUGGESTED_LABELS) + ". You may include "
    "additional categories you find more appropriate; use stable "
    "lower-snake-case identifiers. Probabilities should be in [0,1] "
    "and sum approximately to 1.0. Output ONLY valid JSON: "
    '{"classification": {"<label>": <0..1>, ...}}. '
    "Do not include categories with probability < 0.05."
)


def extract_classification(
    *,
    context: AiContext,
    document_text: str,
) -> dict[str, float]:
    """Score the document against the suggested classification labels.

    Returns an empty dict on miss / non-runnable context — consumers
    branch on emptiness, not on missing keys, so a 0-confidence
    label is meaningful information.
    """
    if not context.runnable:
        return {}
    cleaned = (document_text or "").strip()
    if not cleaned:
        return {}
    sample = cleaned[:_MAX_TEXT_CHARS]
    response = call_text_model(
        budget=context.budget,
        signal_kind=SIGNAL_KIND,
        system=_SYSTEM,
        prompt=sample,
        max_tokens=256,
    )
    if not response:
        return {}
    parsed = parse_json_payload(response)
    if not isinstance(parsed, dict):
        logger.warning(
            "classification extractor: non-dict response: %r", response[:200]
        )
        return {}
    labels = parsed.get("classification")
    if not isinstance(labels, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not key:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        score = max(0.0, min(1.0, score))
        if score >= 0.05:
            out[key] = score
    return out
