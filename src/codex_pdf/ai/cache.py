"""Per-kind cache for AI signal extraction.

Each signal kind has a deterministic cache key documented in
``docs/policies.md`` (1.10.0):

- ``language``       → ``(tenant, pdf_hash, page_index, "language")``
- ``logos``          → ``(tenant, pdf_hash, page_index, "logos")``
- ``symbols``        → ``(tenant, pdf_hash, page_index, "symbols")``
- ``barcodes``       → ``(tenant, pdf_hash, page_index, "barcodes")``
- ``spell``          → ``(tenant, pdf_hash, page_index, "spell")``
- ``classification`` → ``(tenant, pdf_hash, "classification")``

Idempotent: same key → same JSON payload across versions.

The underlying store is whatever ``codex_pdf.api.main._cache``
resolves to (Redis when ``CODEX_REDIS_URL`` is set; in-process LRU
otherwise). This module ONLY computes keys + (de)serialises JSON;
it does not own the connection.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from codex_pdf.version import VERSION

logger = logging.getLogger(__name__)

_KIND_TTL_SECONDS = 7 * 24 * 60 * 60  # one week


def signal_cache_key(
    *,
    tenant: str,
    pdf_hash: str,
    kind: str,
    page_index: int | None = None,
) -> str:
    """Build the canonical cache key for one signal.

    Layout: ``codex:{VERSION}:signal:{tenant}:{pdf_hash}:{kind}[:p{idx}]``.

    Document-scoped kinds (``classification``) omit the page suffix.
    """
    base = f"codex:{VERSION}:signal:{tenant}:{pdf_hash}:{kind}"
    if page_index is not None:
        return f"{base}:p{page_index}"
    return base


def get_cached(cache: Any, key: str) -> Any | None:
    """Read a cached signal payload. Returns ``None`` on miss / error."""
    if cache is None:
        return None
    try:
        raw = cache.get(key)
    except Exception:
        logger.exception("signal cache GET failed (key=%s)", key)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("signal cache hit is not JSON (key=%s)", key)
        return None


def set_cached(cache: Any, key: str, value: Any) -> None:
    if cache is None:
        return
    try:
        cache.set(key, json.dumps(value, sort_keys=True), ex=_KIND_TTL_SECONDS)
    except TypeError:
        # MemoryCache.set takes (key, value) without ex; degrade.
        try:
            cache.set(key, json.dumps(value, sort_keys=True))
        except Exception:
            logger.exception("signal cache SET fallback failed (key=%s)", key)
    except Exception:
        logger.exception("signal cache SET failed (key=%s)", key)
