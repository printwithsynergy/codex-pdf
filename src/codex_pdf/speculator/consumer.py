"""Speculator stream consumer.

Reads from Redis Stream ``codex:speculate`` (created lazily on first
XADD by the API) and runs Phase 1 + Phase 2 extracts for each sha,
populating the same ``codex:VERSION:extract*`` cache keys the API
reads. Idempotent: a cache hit short-circuits before any work runs,
so duplicate stream entries cost only one Redis GET.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from typing import Any

from codex_pdf.api.blob_store import PDF_BLOB_KEY_PREFIX, make_blob_store
from codex_pdf.api.cache import cache_key, make_cache
from codex_pdf.extract import extract_document, extract_document_fast

logger = logging.getLogger(__name__)

STREAM_KEY = "codex:speculate"
DEFAULT_BLOCK_MS = 5_000
DEFAULT_BATCH_SIZE = 4


class SpeculatorConsumer:
    """Single-threaded stream reader.

    The class is structured so unit tests can drive it with a fake
    Redis client — see ``process_one_message`` and ``process_batch``.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        cache: Any,
        blob_store_get: Any,
        block_ms: int = DEFAULT_BLOCK_MS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._redis = redis_client
        self._cache = cache
        self._blob_store_get = blob_store_get
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._last_id = "0-0"
        self._stop = threading.Event()
        # Counters surface via /metrics on the sidecar healthcheck app.
        self.processed = 0
        self.skipped_already_cached = 0
        self.blob_missing = 0
        self.errors = 0

    def stop(self) -> None:
        self._stop.set()

    def already_cached(self, raw: bytes) -> bool:
        key = cache_key(raw, {}, kind="extract")
        return self._cache.get(key) is not None

    def process_one_message(self, sha: str, source: str) -> None:
        """Run Phase 1 + Phase 2 for ``sha`` if not already cached."""
        try:
            blob = self._blob_store_get(sha)
        except Exception:
            self.errors += 1
            logger.exception("speculator: blob lookup failed for %s", sha[:16])
            return
        if blob is None:
            self.blob_missing += 1
            logger.debug("speculator: blob missing for %s (source=%s)", sha[:16], source)
            return
        if self.already_cached(blob):
            self.skipped_already_cached += 1
            return

        try:
            t0 = time.perf_counter()
            phase1 = extract_document_fast(blob)
            self._cache.set(
                cache_key(blob, {}, kind="extract-phase-1"),
                json.dumps(phase1.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )

            phase2 = extract_document(blob)
            self._cache.set(
                cache_key(blob, {}, kind="extract"),
                json.dumps(phase2.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            elapsed = time.perf_counter() - t0
            self.processed += 1
            logger.info(
                "speculator: cached sha=%s source=%s in %.2fs",
                sha[:16],
                source,
                elapsed,
            )
        except Exception:
            self.errors += 1
            logger.exception("speculator: extract failed for %s", sha[:16])

    def process_batch(self, entries: list[tuple[str, dict[bytes, bytes]]]) -> None:
        for entry_id, fields in entries:
            self._last_id = entry_id
            try:
                sha = fields.get(b"sha", b"").decode("utf-8")
                source = fields.get(b"source", b"unknown").decode("utf-8")
            except Exception:
                self.errors += 1
                continue
            if not sha:
                continue
            self.process_one_message(sha, source)

    def run_once(self) -> int:
        """Block on ``XREAD`` for one round; return entries processed."""
        try:
            response = self._redis.xread(
                {STREAM_KEY: self._last_id},
                block=self._block_ms,
                count=self._batch_size,
            )
        except Exception:
            self.errors += 1
            logger.exception("speculator: XREAD failed")
            time.sleep(1.0)
            return 0
        if not response:
            return 0
        # response: [(stream_name, [(entry_id, {field: value}), ...])]
        total = 0
        for _stream_name, entries in response:
            self.process_batch(entries)
            total += len(entries)
        return total

    def run_forever(self) -> None:
        logger.info("speculator: starting consumer on stream %s", STREAM_KEY)
        while not self._stop.is_set():
            self.run_once()
        logger.info("speculator: stopped (last_id=%s, processed=%d)", self._last_id, self.processed)


def _build_redis_client():
    redis_url = (os.environ.get("CODEX_REDIS_URL") or "").strip()
    if not redis_url:
        raise RuntimeError(
            "CODEX_REDIS_URL is required to run the speculator — there is "
            "no in-memory fallback (the API and the sidecar must share "
            "the same backing store)."
        )
    import redis  # type: ignore

    client = redis.Redis.from_url(redis_url, socket_connect_timeout=2.0, socket_timeout=10.0)
    if not client.ping():
        raise RuntimeError("speculator: Redis PING returned falsy")
    return client


def run_forever() -> None:
    """Boot the speculator. Used by ``python -m codex_pdf.speculator``."""
    logging.basicConfig(level=os.environ.get("CODEX_LOG_LEVEL", "INFO").upper())

    client = _build_redis_client()
    cache = make_cache()
    blob_store = make_blob_store()

    def blob_get(sha: str) -> bytes | None:
        # Reuse the same key prefix as the API so we read the bytes the
        # API wrote. ``make_blob_store`` would also work, but going
        # through the same Redis client keeps everything on one
        # connection.
        try:
            return client.get(PDF_BLOB_KEY_PREFIX + sha)
        except Exception:
            logger.warning("speculator: blob fetch failed for %s", sha[:16], exc_info=True)
            # Fall back to the configured store (e.g. memory in tests).
            return blob_store.get(sha) if hasattr(blob_store, "get") else None

    consumer = SpeculatorConsumer(
        redis_client=client,
        cache=cache,
        blob_store_get=blob_get,
    )

    def _signal_handler(signum: int, _frame: Any) -> None:  # noqa: ARG001
        logger.info("speculator: caught signal %s; stopping", signum)
        consumer.stop()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    consumer.run_forever()
