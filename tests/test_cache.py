"""Tests for the codex render cache backends.

Exercises the hard contract for 1.3.1: the codex API must boot and
serve requests no matter what the operator did with ``CODEX_REDIS_URL``.

- Unset / empty → in-memory cache, no warning.
- Bogus URL → in-memory fallback, warning logged.
- Reachable URL but PING fails (mocked) → in-memory fallback, warning.
- Reachable URL + PING ok (mocked) → RedisCache returned.
- Transient get/set failures → cache miss / silent skip, never raise.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from codex_pdf.version import VERSION

from codex_pdf.api.cache import (
    MemoryCache,
    RedisCache,
    cache_key,
    make_cache,
)


def test_cache_key_is_content_addressed() -> None:
    a = cache_key(b"%PDF-1", {"page": 1, "dpi": 300}, kind="page")
    b = cache_key(b"%PDF-1", {"page": 1, "dpi": 300}, kind="page")
    c = cache_key(b"%PDF-2", {"page": 1, "dpi": 300}, kind="page")
    d = cache_key(b"%PDF-1", {"page": 1, "dpi": 300}, kind="layer")
    assert a == b
    assert a != c
    assert a != d
    assert a.startswith(f"codex:{VERSION}:page:")


def test_cache_key_separates_tenants() -> None:
    """Two tenants get distinct keys for the same input."""
    a = cache_key(b"%PDF-1", {"page": 1}, kind="page", tenant="tenant-a")
    b = cache_key(b"%PDF-1", {"page": 1}, kind="page", tenant="tenant-b")
    assert a != b
    # Default tenant falls between the literal tenant labels.
    default = cache_key(b"%PDF-1", {"page": 1}, kind="page")
    assert default != a
    assert default != b


def test_cache_key_stable_across_process_restarts() -> None:
    """Cache keys survive process boundaries.

    Codex relies on the cache key being a pure function of
    (VERSION, kind, tenant, pdf_bytes, args). Subprocesses must
    compute the same key from the same inputs so a multi-replica
    deployment (each Python worker its own process) doesn't fragment
    its cache. This test runs the key derivation in a subprocess
    and asserts byte-for-byte identity.
    """
    import subprocess
    import sys

    main_key = cache_key(
        b"%PDF-test\nstable-key-fixture",
        {"page_index": 0, "dpi": 150},
        kind="text-regions",
        tenant="stability-test",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from codex_pdf.api.cache import cache_key; "
                "print(cache_key("
                "b'%PDF-test\\nstable-key-fixture', "
                "{'page_index': 0, 'dpi': 150}, "
                "kind='text-regions', tenant='stability-test'))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess_key = proc.stdout.strip()
    assert subprocess_key == main_key, (subprocess_key, main_key)


def test_memory_cache_round_trip() -> None:
    c = MemoryCache(maxsize=2)
    c.set("a", b"1")
    c.set("b", b"2")
    assert c.get("a") == b"1"
    c.set("c", b"3")  # evicts 'b' (LRU)
    assert c.get("b") is None
    assert c.get("c") == b"3"


def test_make_cache_unset(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("CODEX_REDIS_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        cache = make_cache()
    assert isinstance(cache, MemoryCache)
    # No warning when redis was simply not configured.
    assert all("Redis init failed" not in r.message for r in caplog.records)


def test_make_cache_empty_string(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """An accidentally-empty value is treated the same as unset."""
    monkeypatch.setenv("CODEX_REDIS_URL", "   ")
    cache = make_cache()
    assert isinstance(cache, MemoryCache)


def test_make_cache_bogus_url(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("CODEX_REDIS_URL", "redis://nonexistent.invalid:6379/0")
    with caplog.at_level(logging.WARNING):
        cache = make_cache()
    assert isinstance(cache, MemoryCache)
    assert any("Redis init failed" in r.message for r in caplog.records)


def test_make_cache_redis_ping_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When PING raises, RedisCache.__init__ raises, make_cache falls through."""
    pytest.importorskip("redis")

    class FakeClient:
        def ping(self) -> bool:
            raise ConnectionError("can't reach redis")

    def fake_from_url(*_args: Any, **_kwargs: Any) -> FakeClient:
        return FakeClient()

    import redis  # type: ignore

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    monkeypatch.setenv("CODEX_REDIS_URL", "redis://example.com:6379/0")

    with caplog.at_level(logging.WARNING):
        cache = make_cache()
    assert isinstance(cache, MemoryCache)
    assert any("PING failed" in r.message or "Redis init failed" in r.message for r in caplog.records)


def test_make_cache_redis_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("redis")

    class FakeClient:
        def __init__(self) -> None:
            self.store: dict[str, bytes] = {}

        def ping(self) -> bool:
            return True

        def get(self, k: str) -> bytes | None:
            return self.store.get(k)

        def set(self, k: str, v: bytes, ex: int | None = None) -> None:
            self.store[k] = v

    fake = FakeClient()

    def fake_from_url(*_args: Any, **_kwargs: Any) -> FakeClient:
        return fake

    import redis  # type: ignore

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    monkeypatch.setenv("CODEX_REDIS_URL", "redis://example.com:6379/0")

    cache = make_cache()
    assert isinstance(cache, RedisCache)
    cache.set("k", b"v")
    assert cache.get("k") == b"v"


def test_redis_cache_get_failure_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("redis")

    class FakeClient:
        def ping(self) -> bool:
            return True

        def get(self, _k: str) -> bytes | None:
            raise RuntimeError("transient redis error")

        def set(self, _k: str, _v: bytes, ex: int | None = None) -> None:
            raise RuntimeError("transient redis error")

    def fake_from_url(*_args: Any, **_kwargs: Any) -> FakeClient:
        return FakeClient()

    import redis  # type: ignore

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    monkeypatch.setenv("CODEX_REDIS_URL", "redis://example.com:6379/0")

    cache = make_cache()
    assert isinstance(cache, RedisCache)
    assert cache.get("any") is None  # silent miss
    cache.set("any", b"v")  # silent skip — must not raise
