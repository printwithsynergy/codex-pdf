"""Page-level unknown-word candidate extraction (Claude Haiku).

Emits raw spelling candidates only — codex does NOT apply tenant
spell rules. Lint-pdf consumes this list and runs the tenant-scoped
dictionary check on top. Per the service boundary, codex collects
facts, lint adjudicates.
"""

from __future__ import annotations

import logging

from codex_pdf.ai.claude import call_text_model, parse_json_payload
from codex_pdf.ai.context import AiContext

logger = logging.getLogger(__name__)

SIGNAL_KIND = "spell"
SOURCE = "codex-ai/claude-haiku-4-5"
_MAX_TEXT_CHARS = 6000

_SYSTEM = (
    "You are a spelling-anomaly detector for English-language print "
    "production text. Given the page text, list words you suspect are "
    "misspelled, mis-OCR'd, or otherwise irregular. Include proper "
    "nouns ONLY if they look corrupted (e.g. 'TyIenol'). Exclude valid "
    "brand names, valid acronyms, valid technical terms, valid "
    "abbreviations. Output ONLY valid JSON: "
    '{"candidates": ["word1", "word2", ...]}. '
    "Empty list when none. Do not invent."
)


def extract_spell(
    *,
    context: AiContext,
    page_text: str,
) -> list[str]:
    if not context.runnable:
        return []
    cleaned = (page_text or "").strip()
    if not cleaned:
        return []
    sample = cleaned[:_MAX_TEXT_CHARS]
    response = call_text_model(
        budget=context.budget,
        signal_kind=SIGNAL_KIND,
        system=_SYSTEM,
        prompt=sample,
        max_tokens=512,
    )
    if not response:
        return []
    parsed = parse_json_payload(response)
    if not isinstance(parsed, dict):
        logger.warning("spell extractor: non-dict response: %r", response[:200])
        return []
    items = parsed.get("candidates")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for word in items:
        if not isinstance(word, str):
            continue
        cleaned_word = word.strip()
        if not cleaned_word:
            continue
        # Deduplicate but preserve order (first occurrence wins).
        key = cleaned_word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned_word)
    return out
