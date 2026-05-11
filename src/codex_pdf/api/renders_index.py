"""Renders index: a side-track of the render cache.

The render cache key is opaque to the cache backend (a hashed
content-address), so we can't enumerate cached renders by walking
keys. Instead, the render endpoint writes a small JSON manifest of
``(page_index, dpi, color_space)`` tuples for the PDF alongside each
write, keyed by ``codex:{VERSION}:renders-index:{pdf_hash}``.
``GET /v1/documents/{pdf_hash}/renders`` reads it back.

Idempotent: writing the same tuple twice is a no-op. Eviction is on
the cache backend's TTL; consumers should refresh the index by
re-rendering if they need it back.
"""

from __future__ import annotations

import json
from typing import Any

from codex_pdf.version import VERSION


def _index_key(pdf_hash: str) -> str:
    return f"codex:{VERSION}:renders-index:{pdf_hash}"


def record_render(
    cache: Any,
    pdf_hash: str,
    *,
    page_index: int,
    dpi: int,
    color_space: str,
) -> None:
    """Add ``(page_index, dpi, color_space)`` to the index for this PDF.

    Best-effort: any backend failure is swallowed so a stale index
    can never block a render. ``cache`` must support ``get(key)`` →
    bytes-or-None and ``set(key, bytes)``.
    """
    entry = {
        "page_index": page_index,
        "dpi": dpi,
        "color_space": color_space,
    }
    try:
        existing_raw = cache.get(_index_key(pdf_hash))
        if existing_raw is None:
            entries = [entry]
        else:
            try:
                entries = json.loads(existing_raw)
            except (json.JSONDecodeError, TypeError):
                entries = [entry]
            else:
                if not isinstance(entries, list):
                    entries = [entry]
                else:
                    if entry not in entries:
                        entries.append(entry)
        cache.set(
            _index_key(pdf_hash),
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
    except Exception:
        pass


def list_renders(cache: Any, pdf_hash: str) -> list[dict[str, Any]]:
    """Return the list of cached ``(page_index, dpi, color_space)`` entries.

    Returns an empty list if the index is missing, malformed, or the
    cache backend errors. Consumers reading the list should treat
    unknown ``color_space`` strings as opaque.
    """
    try:
        raw = cache.get(_index_key(pdf_hash))
    except Exception:
        return []
    if raw is None:
        return []
    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        page_index = entry.get("page_index")
        dpi = entry.get("dpi")
        color_space = entry.get("color_space")
        if (
            isinstance(page_index, int)
            and isinstance(dpi, int)
            and isinstance(color_space, str)
        ):
            out.append(
                {"page_index": page_index, "dpi": dpi, "color_space": color_space}
            )
    return out
