"""Codex API cache backends.

Content-addressed by ``VERSION + sha256(pdf) + sha256(args_json)``.
Including ``VERSION`` avoids stale cross-release cache hits when
operators reuse the same Redis backend across deploys. Pluggable:

- ``memory`` (default) — in-process LRU. Always works, no deps.
- ``redis`` — set ``CODEX_REDIS_URL=redis://...``. **Optional**: any
  failure to import ``redis``, parse the URL, connect, or PING is
  logged and the service falls back to the in-memory cache. Redis
  must never crash the codex API at boot or at request time.
- ``s3`` — placeholder; not wired here. Front the API with a CDN /
  object-store cache instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any

from codex_pdf.version import VERSION

logger = logging.getLogger(__name__)


def cache_key(
    pdf_bytes: bytes,
    args: dict[str, Any],
    *,
    kind: str,
    tenant: str = "default",
) -> str:
    """Content-addressed cache key for one render result.

    ``kind`` segregates by endpoint family ("page", "separations",
    "heatmap", "layer", "sample-color", "sample-density",
    "walk-content-stream") so identical args from different endpoints
    don't collide.

    ``tenant`` scopes the key per :data:`CODEX_TENANT` so a hash that
    one tenant cached can't be read by another. Defaults to
    ``"default"`` for unauthenticated / single-tenant deployments;
    callers derive the value from the ``X-Codex-Tenant`` request
    header via :func:`codex_pdf.api.retention.normalise_tenant`.
    """
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    args_blob = json.dumps(args, sort_keys=True, separators=(",", ":")).encode("utf-8")
    args_sha = hashlib.sha256(args_blob).hexdigest()
    return f"codex:{VERSION}:{kind}:{tenant}:{pdf_sha}:{args_sha}"


class MemoryCache:
    """Simple LRU. ``maxsize`` defaults to 256 entries."""

    name = "memory"

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
    """Thin wrapper over redis-py. Imported lazily.

    Constructor performs a single ``PING`` at init time so a bogus URL
    or unreachable Redis service surfaces as a startup-time
    ``RuntimeError`` (caught by :func:`make_cache` and downgraded to
    a logged warning). Once initialised, transient ``get``/``set``
    failures are logged and treated as cache misses — they never
    propagate to the API request handler.
    """

    name = "redis"

    def __init__(self, url: str, ttl_seconds: int = 86400) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "CODEX_REDIS_URL set but the 'redis' Python package is not installed. "
                "Install codex-pdf with the 'redis' extra, or unset CODEX_REDIS_URL "
                "to use the in-memory cache."
            ) from exc

        try:
            self._client = redis.Redis.from_url(url, socket_connect_timeout=2.0, socket_timeout=2.0)
        except Exception as exc:
            raise RuntimeError(f"failed to parse CODEX_REDIS_URL: {exc}") from exc

        try:
            pong = self._client.ping()
        except Exception as exc:
            raise RuntimeError(f"Redis PING failed for {url!r}: {exc}") from exc
        if not pong:
            raise RuntimeError(f"Redis at {url!r} did not return PONG")

        self._ttl = ttl_seconds

    def get(self, key: str) -> bytes | None:
        try:
            return self._client.get(key)
        except Exception:
            logger.warning("RedisCache.get failed for %s; treating as miss", key, exc_info=True)
            return None

    def set(self, key: str, value: bytes) -> None:
        try:
            self._client.set(key, value, ex=self._ttl)
        except Exception:
            logger.warning("RedisCache.set failed for %s; cache write skipped", key, exc_info=True)


def make_cache():
    """Pick the cache backend based on environment.

    Order of preference:

    1. ``CODEX_REDIS_URL`` set + non-empty + reachable → ``RedisCache``
    2. Anything else (URL missing, empty, unreachable, malformed) →
       ``MemoryCache`` with a logged warning so operators can spot the
       fallback in deploy logs.

    TTL knob: ``CODEX_CACHE_TTL_SECONDS`` (default 86400 / 24h) applies
    to ``RedisCache`` SETEX. ``MemoryCache`` is LRU-only and ignores the
    TTL — entries live until evicted by size pressure. The
    operator-facing knob is the single source of truth for "how long
    can a derived artifact live" across both backends.

    This function MUST NOT raise. The codex API treats the cache as a
    soft dependency: even if every backend is misconfigured the
    service must still come up and serve requests (cache misses on
    every call, but functional).
    """
    redis_url = (os.environ.get("CODEX_REDIS_URL") or "").strip()
    ttl = _ttl_from_env()
    if not redis_url:
        logger.info(
            "codex cache: in-memory (CODEX_REDIS_URL unset; TTL knob ignored, LRU only)"
        )
        return MemoryCache()
    try:
        cache = RedisCache(redis_url, ttl_seconds=ttl)
        logger.info(
            "codex cache: redis (%s, ttl=%ds)", _mask_url(redis_url), ttl
        )
        return cache
    except Exception as exc:
        logger.warning(
            "codex cache: Redis init failed (%s); falling back to in-memory cache. "
            "This is safe but cold-cache CPU is higher. Fix CODEX_REDIS_URL or "
            "delete the redis service to silence this warning.",
            exc,
        )
        return MemoryCache()


def _mask_url(url: str) -> str:
    """Strip credentials from a Redis URL before logging it."""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.password or parsed.username:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "<redacted>"


_DEFAULT_CACHE_TTL_SECONDS = 86400


def _ttl_from_env() -> int:
    """Resolve the operator-facing cache TTL knob.

    Reads ``CODEX_CACHE_TTL_SECONDS`` (default 86400 / 24h). Invalid
    values fall back to the default with a warning — a typo'd env
    must never break service boot.
    """
    raw = (os.environ.get("CODEX_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "CODEX_CACHE_TTL_SECONDS=%r is not an integer; using default %ds",
            raw,
            _DEFAULT_CACHE_TTL_SECONDS,
        )
        return _DEFAULT_CACHE_TTL_SECONDS
    if value <= 0:
        logger.warning(
            "CODEX_CACHE_TTL_SECONDS=%d must be positive; using default %ds",
            value,
            _DEFAULT_CACHE_TTL_SECONDS,
        )
        return _DEFAULT_CACHE_TTL_SECONDS
    return value
