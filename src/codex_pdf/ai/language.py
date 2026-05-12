"""Page-level dominant-language detection (Claude Haiku)."""

from __future__ import annotations

import logging

from codex_pdf.ai.claude import call_text_model, parse_json_payload
from codex_pdf.ai.context import AiContext
from codex_pdf.models.v1 import CodexDetectedLanguage

logger = logging.getLogger(__name__)

SIGNAL_KIND = "language"
SOURCE = "codex-ai/claude-haiku-4-5"
_MAX_TEXT_CHARS = 4000  # ~1300 tokens at 3 chars/token

_SYSTEM = (
    "You are a language identification model. Identify the dominant "
    "natural language of the supplied text. Output ONLY valid JSON, "
    "no prose, with the shape: "
    '{"code": "<bcp47-tag>", "confidence": <0.0..1.0>}.'
)


def extract_language(
    *,
    context: AiContext,
    page_text: str,
) -> CodexDetectedLanguage | None:
    """Detect the dominant language on a page.

    Returns ``None`` when codex has no text to score (image-only
    page); the consumer renders that as "no language signal" — not
    "language is empty string".
    """
    if not context.runnable:
        return None
    cleaned = (page_text or "").strip()
    if not cleaned:
        return None
    sample = cleaned[:_MAX_TEXT_CHARS]
    raw = call_text_model(
        budget=context.budget,
        signal_kind=SIGNAL_KIND,
        system=_SYSTEM,
        prompt=sample,
        max_tokens=64,
    )
    if not raw:
        return None
    parsed = parse_json_payload(raw)
    if not isinstance(parsed, dict):
        logger.warning("language extractor: non-dict response: %r", raw[:200])
        return None
    code = parsed.get("code")
    if not isinstance(code, str) or not code:
        return None
    try:
        confidence = float(parsed.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = max(0.0, min(1.0, confidence))
    return CodexDetectedLanguage(code=code, confidence=confidence, source=SOURCE)
