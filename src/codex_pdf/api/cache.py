"""Codex API cache backends.

Content-addressed by ``sha256(pdf) + sha256(args_json)``. Pluggable:

- ``memory`` (default) — in-process LRU.
- ``redis`` — set ``CODEX_REDIS_URL=redis://...``.
- ``s3`` — set ``CODEX_S3_BUCKET`` (uses default AWS creds chain).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


def cache_key(pdf_bytes: bytes, args: dict[str, Any], *, kind: str) -> str:
    """Content-addressed cache key for one render result.

    ``kind`` segregates by endpoint family ("page", "separations",
    "heatmap", "layer", "sample-color", "sample-density",
    "walk-content-stream") so identical args from different endpoints
    don't collide.
    """
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    args_blob = json.dumps(args, sort_keys=True, separators=(",", ":")).encode("utf-8")
    args_sha = hashlib.sha256(args_blob).hexdigest()
    return f"codex:{kind}:{pdf_sha}:{args_sha}"


class MemoryCache:
    """Simple LRU. ``maxsize`` defaults to 256 entries."""

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._store: OrderedDict[str, bytes] = OrderedDict()

    def get(self, key: str) -> bytes | None:
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def set(self, key: str, value: bytes) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)


class RedisCache:
    """Thin wrapper over redis-py. Imported lazily."""

    def __init__(self, url: str, ttl_seconds: int = 86400) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "CODEX_REDIS_URL set but 'redis' Python package not installed. "
                "Add 'redis' to your environment."
            ) from exc
        self._client = redis.Redis.from_url(url)
        self._ttl = ttl_seconds

    def get(self, key: str) -> bytes | None:
        try:
            return self._client.get(key)
        except Exception:
            logger.exception("RedisCache.get failed for %s", key)
            return None

    def set(self, key: str, value: bytes) -> None:
        try:
            self._client.set(key, value, ex=self._ttl)
        except Exception:
            logger.exception("RedisCache.set failed for %s", key)


def make_cache():
    """Pick the cache backend based on environment.

    Order: ``CODEX_REDIS_URL`` → ``RedisCache``; otherwise ``MemoryCache``.
    S3 is intentionally not wired here yet — sites that need S3 should
    front the API with a CDN/object-store cache instead of mixing it
    into the API request path.
    """
    redis_url = os.environ.get("CODEX_REDIS_URL")
    if redis_url:
        try:
            return RedisCache(redis_url)
        except Exception:
            logger.exception("Falling back to MemoryCache; RedisCache init failed")
    return MemoryCache()
