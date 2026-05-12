"""Codex PDF blob store.

Caches raw PDF bytes by ``sha256(pdf)`` so clients (loupe-pdf,
lint-pdf) can avoid re-uploading the same PDF on every render call.
Each blob is scoped by ``tenant`` so a hash uploaded by Tenant A
isn't readable by Tenant B even if the hash leaks elsewhere.
Defaults to ``"default"`` for single-tenant deployments.

After ``/v1/extract`` runs, the source PDF is stashed here and its
sha256 is returned to the client in the codex document. Subsequent
render / sample / walk calls may pass ``pdf_sha256`` instead of a
multipart ``pdf`` upload — the server looks up the bytes by
``(tenant, hash)`` and proceeds normally. If the hash has expired
(TTL) the server returns ``412 Precondition Failed`` and the client
re-uploads.

Default TTL is 60 minutes — long enough for an interactive viewer
session but short enough that idle blobs don't accumulate. Backed
by Redis when ``CODEX_REDIS_URL`` is set, in-memory dict otherwise.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict

logger = logging.getLogger(__name__)

PDF_BLOB_KEY_PREFIX = "codex:pdf-blob:"
DEFAULT_BLOB_TTL_SECONDS = 3600
DEFAULT_BLOB_MAX_BYTES = 500 * 1024 * 1024  # 500 MB across all blobs in memory mode


def _scoped(sha256: str, tenant: str) -> str:
    """Internal key: tenant + sha256. Tenant collisions across the
    same hash are impossible because each tenant gets its own slot.
    """
    return f"{tenant}:{sha256}"


class MemoryBlobStore:
    """In-process blob store with size-bound LRU eviction.

    Eviction is triggered when total cached bytes exceed
    ``max_bytes``. TTL is approximate — entries are checked at
    access time but not actively swept.
    """

    name = "memory"

    def __init__(self, max_bytes: int = DEFAULT_BLOB_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._store: OrderedDict[str, bytes] = OrderedDict()
        self._total_bytes = 0

    def put(self, sha256: str, pdf_bytes: bytes, *, tenant: str = "default") -> None:
        key = _scoped(sha256, tenant)
        if key in self._store:
            self._total_bytes -= len(self._store[key])
            self._store.move_to_end(key)
        self._store[key] = pdf_bytes
        self._total_bytes += len(pdf_bytes)
        while self._total_bytes > self._max_bytes and len(self._store) > 1:
            _, evicted = self._store.popitem(last=False)
            self._total_bytes -= len(evicted)

    def get(self, sha256: str, *, tenant: str = "default") -> bytes | None:
        key = _scoped(sha256, tenant)
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None


class RedisBlobStore:
    """Redis-backed blob store. Each blob has its own TTL via SETEX."""

    name = "redis"

    def __init__(self, url: str, ttl_seconds: int = DEFAULT_BLOB_TTL_SECONDS) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "CODEX_REDIS_URL set but the 'redis' Python package is not installed."
            ) from exc

        try:
            self._client = redis.Redis.from_url(
                url, socket_connect_timeout=2.0, socket_timeout=5.0
            )
        except Exception as exc:
            raise RuntimeError(f"failed to parse CODEX_REDIS_URL: {exc}") from exc

        try:
            pong = self._client.ping()
        except Exception as exc:
            raise RuntimeError(f"Redis PING failed for {url!r}: {exc}") from exc
        if not pong:
            raise RuntimeError(f"Redis at {url!r} did not return PONG")

        self._ttl = ttl_seconds

    def put(self, sha256: str, pdf_bytes: bytes, *, tenant: str = "default") -> None:
        try:
            self._client.set(
                PDF_BLOB_KEY_PREFIX + _scoped(sha256, tenant),
                pdf_bytes,
                ex=self._ttl,
            )
        except Exception:
            logger.warning(
                "RedisBlobStore.put failed for %s/%s; blob not cached",
                tenant,
                sha256[:16],
                exc_info=True,
            )
            return
        # Hand the sha to codex-speculator so Phase 2 is warm by the
        # time the client follows up with /v1/extract. Best-effort —
        # any failure (no consumer attached, stream pruned, write
        # timeout) is logged and swallowed. A 60 s SET-NX dedupe key
        # collapses repeated puts for the same sha into a single XADD.
        try:
            if self._client.set(
                f"codex:speculate:dedupe:{tenant}:{sha256}",
                "blob_put",
                ex=60,
                nx=True,
            ):
                self._client.xadd(
                    "codex:speculate",
                    {"sha": sha256, "tenant": tenant, "source": "blob_put"},
                    maxlen=10000,
                    approximate=True,
                )
        except Exception:
            logger.debug(
                "speculate publish failed for %s/%s on blob put",
                tenant,
                sha256[:16],
                exc_info=True,
            )

    def get(self, sha256: str, *, tenant: str = "default") -> bytes | None:
        try:
            return self._client.get(PDF_BLOB_KEY_PREFIX + _scoped(sha256, tenant))
        except Exception:
            logger.warning(
                "RedisBlobStore.get failed for %s/%s; treating as miss",
                tenant,
                sha256[:16],
                exc_info=True,
            )
            return None


def make_blob_store():
    """Pick a blob store backend based on environment.

    Mirrors :func:`codex_pdf.api.cache.make_cache` — Redis when
    ``CODEX_REDIS_URL`` is set and reachable, in-memory dict
    otherwise. MUST NOT raise on misconfiguration; the codex API
    treats the blob store as a soft dependency.
    """
    redis_url = (os.environ.get("CODEX_REDIS_URL") or "").strip()
    ttl = int(os.environ.get("CODEX_PDF_BLOB_TTL_SECONDS") or DEFAULT_BLOB_TTL_SECONDS)
    if not redis_url:
        logger.info("codex blob store: in-memory (CODEX_REDIS_URL unset)")
        return MemoryBlobStore()
    try:
        store = RedisBlobStore(redis_url, ttl_seconds=ttl)
        logger.info("codex blob store: redis (ttl=%ds)", ttl)
        return store
    except Exception as exc:
        logger.warning(
            "codex blob store: Redis init failed (%s); falling back to in-memory store.",
            exc,
        )
        return MemoryBlobStore()
