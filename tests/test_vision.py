"""codex-vision-sidecar (Phase 1.5) tests.

The vision sidecar is its own FastAPI app exposing the CPU
extractors over HTTP. These tests pin the contract surface
(`/healthz`, `/v1/contract`, `/v1/vision/phash`) and the client's
graceful-degradation when ``CODEX_VISION_URL`` is unset.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def vision_client() -> TestClient:
    from codex_pdf.vision.app import app

    return TestClient(app)


def test_healthz_reports_phash_extractor(vision_client: TestClient) -> None:
    resp = vision_client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["extractors"]["phash"] is True
    # schema version + package version are both pinned strings.
    assert "schema_version" in body
    assert "package_version" in body


def test_contract_lists_phash_endpoint(vision_client: TestClient) -> None:
    resp = vision_client.get("/v1/contract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_name"] == "codex-vision"
    assert "POST /v1/vision/phash" in body["endpoints"]


def test_phash_endpoint_500_on_garbage(vision_client: TestClient) -> None:
    """The pHash endpoint returns 500 when the input isn't a parseable
    image (Pillow rejects it). 4xx is reserved for client errors —
    a corrupt raster is a client-error in spirit but the existing
    contract surfaces it as 500 with a structured detail."""
    resp = vision_client.post(
        "/v1/vision/phash",
        files={"image": ("page.bin", b"not an image", "application/octet-stream")},
    )
    # Either Pillow rejects (500) or imagehash isn't installed (500
    # with a different detail). Both are valid "unable to compute"
    # paths; the contract is "200 on success, 500 on inability".
    assert resp.status_code == 500


def test_client_returns_none_when_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from codex_pdf.vision import client

    monkeypatch.delenv("CODEX_VISION_URL", raising=False)
    assert client.is_configured() is False
    assert client.compute_phash(b"any-png-bytes") is None
    assert client.healthcheck() is False
