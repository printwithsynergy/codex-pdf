"""Pytest session fixtures.

Defensively clears codex auth / fetch env vars at session start so a
shell that exported real production credentials (e.g. by sourcing
/tmp/codex-creds.env during a deploy run) doesn't leak into the
unit-test process and 401 every request.
"""

from __future__ import annotations

import os

import pytest


_SCRUBBED_VARS: tuple[str, ...] = (
    "CODEX_AUTH_MODE",
    "CODEX_BEARER_TOKEN",
    "CODEX_API_KEY",
    "CODEX_INTERNAL_TOKEN",
    "CODEX_BASIC_AUTH_ENABLED",
    "CODEX_BASIC_AUTH_USERNAME",
    "CODEX_BASIC_AUTH_PASSWORD",
    "ALLOW_EXTERNAL_FETCH",
    "FETCH_MAX_BYTES",
    "FETCH_TIMEOUT_MS",
    "FETCH_MAX_REDIRECTS",
    "CODEX_FETCH_ALLOW_PRIVATE",
    "CODEX_API_BASE",
    "CODEX_API_BASES",
    "CODEX_API_POOL_JSON",
    "CODEX_ROUTE_MODE",
    "CODEX_PLANT",
    "CODEX_AFFINITY_KEY",
    "CODEX_PLANT_AFFINITY_KEY",
    "CODEX_REQUIRED_SECTION_VERSIONS",
    "CODEX_LOCAL_FALLBACK",
)


@pytest.fixture(autouse=True)
def _scrub_codex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SCRUBBED_VARS:
        monkeypatch.delenv(name, raising=False)
    # Most tests assume the API runs in open mode; specific tests opt
    # back in to auth via their own monkeypatch.
    yield
