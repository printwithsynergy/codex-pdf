"""Thin Anthropic SDK wrapper for AI signal extractors.

Centralises:

- lazy import (``anthropic`` is optional)
- a singleton client (one TCP pool per process)
- prompt-cached system blocks (``cache_control={"type": "ephemeral"}``)
  so repeat extractions on similar PDFs hit Anthropic's 1-hour
  prompt cache and pay 10x less on cached input tokens

Each extractor lives in its own module and imports
:func:`call_text_model` or :func:`call_vision_model` from here.

The two helpers always go through :class:`~codex_pdf.ai.budget.AiBudget`
so the cost cap is honoured uniformly.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from codex_pdf.ai.budget import AiBudget

logger = logging.getLogger(__name__)

DEFAULT_TEXT_MODEL = "claude-haiku-4-5"
DEFAULT_VISION_MODEL = "claude-sonnet-4-6"


@lru_cache(maxsize=1)
def _client() -> Any:
    """Lazy singleton Anthropic client.

    Returns ``None`` when the SDK isn't installed or the API key is
    missing — callers handle ``None`` as a degraded path (signal
    fields stay empty + ``ai_missing_credentials`` warning).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=30.0)


def _estimate_tokens(text: str) -> int:
    """Cheap upper-bound on token count.

    Anthropic doesn't publish a tokenizer in the public SDK; the
    Claude family tokenises roughly at 3.5 chars/token. We round up
    to 3 chars/token to make the cap conservative — better to refuse
    a borderline call than blow the budget.
    """
    return max(1, (len(text) + 2) // 3)


def call_text_model(
    *,
    budget: AiBudget,
    signal_kind: str,
    system: str,
    prompt: str,
    model: str = DEFAULT_TEXT_MODEL,
    max_tokens: int = 512,
) -> str:
    """Run a Claude call with text-only input. Returns the assistant text.

    Empty string is returned when the call cannot run (missing
    credentials) so extractors can default to empty signal fields
    without a try/except dance. The caller still sees an
    ``ai_missing_credentials`` warning via the context status, so
    this is not silent failure.
    """
    client = _client()
    if client is None:
        return ""
    input_tokens = _estimate_tokens(system) + _estimate_tokens(prompt)
    budget.admit(
        kind=signal_kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=max_tokens,
        images=0,
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.exception("claude text call failed (kind=%s)", signal_kind)
        return ""
    return _extract_text(response)


def call_vision_model(
    *,
    budget: AiBudget,
    signal_kind: str,
    system: str,
    prompt: str,
    images_b64: list[tuple[str, str]],
    model: str = DEFAULT_VISION_MODEL,
    max_tokens: int = 1024,
) -> str:
    """Run a Claude call with image input. ``images_b64`` is
    a list of ``(media_type, base64_payload)`` tuples.
    """
    client = _client()
    if client is None:
        return ""
    if not images_b64:
        return call_text_model(
            budget=budget,
            signal_kind=signal_kind,
            system=system,
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
        )
    input_tokens = _estimate_tokens(system) + _estimate_tokens(prompt)
    budget.admit(
        kind=signal_kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=max_tokens,
        images=len(images_b64),
    )
    content: list[dict[str, Any]] = []
    for media_type, b64 in images_b64:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            }
        )
    content.append({"type": "text", "text": prompt})
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content}],
        )
    except Exception:
        logger.exception("claude vision call failed (kind=%s)", signal_kind)
        return ""
    return _extract_text(response)


def _extract_text(response: Any) -> str:
    parts = getattr(response, "content", None) or []
    out: list[str] = []
    for block in parts:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "".join(out).strip()


def parse_json_payload(text: str) -> dict[str, Any] | list[Any] | None:
    """Extract a JSON object/array from a Claude response.

    Claude reliably emits JSON when asked to, but sometimes wraps it
    in markdown fences (`````json``). This helper
    strips fences, finds the first complete top-level structure, and
    parses it. Returns ``None`` on parse failure.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start = -1
    for idx, ch in enumerate(cleaned):
        if ch in "[{":
            start = idx
            break
    if start < 0:
        return None
    cleaned = cleaned[start:]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Try trimming trailing prose — some calls add a sentence after
    # the JSON. Find the matching close-bracket and try again.
    depth = 0
    opener = cleaned[0]
    closer = "}" if opener == "{" else "]"
    for idx, ch in enumerate(cleaned):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[: idx + 1])
                except json.JSONDecodeError:
                    return None
    return None
