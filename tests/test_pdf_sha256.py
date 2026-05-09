"""Test the new pdf_sha256 round-trip flow."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codex_pdf.api.main import app, _blob_store

PDF_PATH = Path(__file__).parent / "fixtures" / "conforming" / "minimal.pdf"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_extract_returns_pdf_sha256(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    expected_sha = hashlib.sha256(pdf_bytes).hexdigest()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("pdf_sha256") == expected_sha
    # Server should have stashed the bytes for follow-up calls.
    assert _blob_store.get(expected_sha) == pdf_bytes


def test_walk_content_stream_accepts_pdf_sha256(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    _blob_store.put(sha, pdf_bytes)

    resp = client.post(
        "/v1/walk/content-stream",
        data={"page": "1", "pdf_sha256": sha},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_num"] == 1
    assert "signals" in body


def test_walk_content_stream_412_when_blob_missing(client: TestClient) -> None:
    bogus_sha = "0" * 64
    resp = client.post(
        "/v1/walk/content-stream",
        data={"page": "1", "pdf_sha256": bogus_sha},
    )
    assert resp.status_code == 412
    assert "not in cache" in resp.json()["detail"]


def test_endpoint_400_when_neither_pdf_nor_sha_provided(client: TestClient) -> None:
    resp = client.post("/v1/walk/content-stream", data={"page": "1"})
    assert resp.status_code == 400
    assert "pdf" in resp.json()["detail"].lower()
