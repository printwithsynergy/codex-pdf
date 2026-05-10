"""Tests for codex_pdf.speculator.consumer.

These exercise the consumer with an in-memory fake Redis client + the
real cache/blob_store. They cover three behaviours that matter for
correctness more than the rest:

1. A stream message for a sha that's already cached short-circuits —
   no extract runs.
2. A stream message for a sha that's NOT cached runs both Phase 1 and
   Phase 2, and writes both kinds to the cache.
3. A stream message for a sha whose blob has expired increments the
   ``blob_missing`` counter and does not raise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codex_pdf.api.cache import MemoryCache, cache_key
from codex_pdf.speculator.consumer import SpeculatorConsumer


PDF_PATH = Path(__file__).parent / "fixtures" / "conforming" / "minimal.pdf"


@pytest.fixture
def pdf_bytes() -> bytes:
    return PDF_PATH.read_bytes()


@pytest.fixture
def pdf_sha(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def make_consumer(blob_table: dict[str, bytes]) -> tuple[SpeculatorConsumer, MemoryCache]:
    cache = MemoryCache()

    def blob_get(sha: str) -> bytes | None:
        return blob_table.get(sha)

    consumer = SpeculatorConsumer(
        redis_client=None,
        cache=cache,
        blob_store_get=blob_get,
    )
    return consumer, cache


def test_speculator_skips_when_cache_hit(pdf_bytes: bytes, pdf_sha: str) -> None:
    consumer, cache = make_consumer({pdf_sha: pdf_bytes})
    cache.set(cache_key(pdf_bytes, {}, kind="extract"), b'{"already":"cached"}')
    consumer.process_one_message(pdf_sha, "probe")
    assert consumer.skipped_already_cached == 1
    assert consumer.processed == 0


def test_speculator_runs_both_phases_on_miss(pdf_bytes: bytes, pdf_sha: str) -> None:
    consumer, cache = make_consumer({pdf_sha: pdf_bytes})
    consumer.process_one_message(pdf_sha, "probe")
    assert consumer.processed == 1
    # Both Phase 1 and Phase 2 keys should now be populated.
    assert cache.get(cache_key(pdf_bytes, {}, kind="extract-phase-1")) is not None
    assert cache.get(cache_key(pdf_bytes, {}, kind="extract")) is not None


def test_speculator_blob_missing_does_not_raise(pdf_sha: str) -> None:
    consumer, _ = make_consumer({})
    consumer.process_one_message(pdf_sha, "blob_put")
    assert consumer.blob_missing == 1
    assert consumer.processed == 0
    assert consumer.errors == 0


def test_speculator_replays_are_idempotent(pdf_bytes: bytes, pdf_sha: str) -> None:
    consumer, cache = make_consumer({pdf_sha: pdf_bytes})
    consumer.process_one_message(pdf_sha, "probe")
    # Second + third deliveries should hit the cache short-circuit.
    consumer.process_one_message(pdf_sha, "probe")
    consumer.process_one_message(pdf_sha, "blob_put")
    assert consumer.processed == 1
    assert consumer.skipped_already_cached == 2
    assert cache.get(cache_key(pdf_bytes, {}, kind="extract")) is not None


def test_speculator_process_batch_drives_last_id(pdf_bytes: bytes, pdf_sha: str) -> None:
    consumer, _ = make_consumer({pdf_sha: pdf_bytes})
    consumer.process_batch(
        [
            ("1700000000-0", {b"sha": pdf_sha.encode("utf-8"), b"source": b"probe"}),
            ("1700000001-0", {b"sha": pdf_sha.encode("utf-8"), b"source": b"blob_put"}),
        ]
    )
    assert consumer._last_id == "1700000001-0"
    assert consumer.processed == 1
    assert consumer.skipped_already_cached == 1
