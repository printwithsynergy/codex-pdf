from __future__ import annotations

import json
from typing import Any

from codex_pdf.client.http_client import HttpClient


class _FakeHeaders(dict):
    def items(self):  # type: ignore[override]
        return super().items()


class _FakeResponse:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self._status = status
        self._body = json.dumps(body).encode("utf-8")
        self.headers = _FakeHeaders({"Content-Type": "application/json"})

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


def test_http_client_fails_over_on_contract_mismatch(monkeypatch) -> None:
    calls: list[str] = []

    def _urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
        url = req.full_url
        calls.append(url)
        if url.startswith("https://codex-a.example.com") and url.endswith("/v1/contract"):
            return _FakeResponse(
                200,
                {
                    "contract_name": "codex-document",
                    "section_schema_versions": {"color": "0.9.0", "geom": "1.0.0"},
                },
            )
        if url.startswith("https://codex-b.example.com") and url.endswith("/v1/contract"):
            return _FakeResponse(
                200,
                {
                    "contract_name": "codex-document",
                    "section_schema_versions": {"color": "1.0.0", "geom": "1.0.0"},
                },
            )
        if url.startswith("https://codex-b.example.com") and url.endswith("/v1/healthz"):
            return _FakeResponse(200, {"status": "ok", "version": "1.4.2", "ghostscript": True})
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setattr("codex_pdf.client.http_client.urlrequest.urlopen", _urlopen)
    client = HttpClient(
        base_urls=["https://codex-a.example.com", "https://codex-b.example.com"],
        route_mode="hybrid",
        required_section_versions={"color": "1.0.0"},
        local_fallback=False,
    )
    health = client.healthz()
    assert health["version"] == "1.4.2"
    assert any(u.startswith("https://codex-a.example.com") for u in calls)
    assert any(u.startswith("https://codex-b.example.com") for u in calls)


def test_http_client_emits_route_context_headers(monkeypatch) -> None:
    captured_headers: dict[str, str] = {}

    def _urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
        nonlocal captured_headers
        captured_headers = dict(req.header_items())
        if req.full_url.endswith("/v1/contract"):
            return _FakeResponse(
                200,
                {
                    "contract_name": "codex-document",
                    "section_schema_versions": {"color": "1.0.0", "geom": "1.0.0"},
                },
            )
        return _FakeResponse(200, {"status": "ok", "version": "1.4.2", "ghostscript": True})

    monkeypatch.setattr("codex_pdf.client.http_client.urlrequest.urlopen", _urlopen)
    client = HttpClient(
        base_url="https://codex-a.example.com",
        plant="plant-a",
        route_mode="hybrid",
        affinity_key="order-123",
        required_section_versions={"color": "1.0.0"},
        local_fallback=False,
    )
    _ = client.healthz()
    lower = {k.lower(): v for k, v in captured_headers.items()}
    assert lower.get("x-codex-plant") == "plant-a"
    assert lower.get("x-codex-route-mode") == "hybrid"
    assert lower.get("x-codex-affinity-key") == "order-123"
    assert "x-codex-request-id" in lower
