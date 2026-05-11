"""Integration tests for opt-in retention through the FastAPI surface.

The S3 client is a ``MagicMock`` swapped onto ``main._retention_store``
for the duration of the test — gives us exact call-arg verification
without taking a dep on ``moto`` or a live S3.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from codex_pdf.api import main as api_main
from codex_pdf.api.retention import RetentionConfig, RetentionStore

FIXTURES = Path(__file__).parent / "fixtures" / "conforming"
PDF_PATH = FIXTURES / "minimal.pdf"


@pytest.fixture
def s3() -> MagicMock:
    return MagicMock()


@pytest.fixture
def retention_store(s3: MagicMock) -> RetentionStore:
    cfg = RetentionConfig(
        bucket="codex-test",
        prefix="codex/test",
        ttl_days=90,
        endpoint_url=None,
        region="us-east-1",
        access_key_id=None,
        secret_access_key=None,
    )
    return RetentionStore(cfg, s3)


@pytest.fixture
def client_with_retention(
    retention_store: RetentionStore, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setattr(api_main, "_retention_store", retention_store)
    return TestClient(api_main.app)


@pytest.fixture
def client_without_retention(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_main, "_retention_store", None)
    return TestClient(api_main.app)


def test_extract_opt_in_writes_three_objects(
    client_with_retention: TestClient, s3: MagicMock
) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client_with_retention.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        data={"retain_for_training": "true"},
        headers={"X-Codex-Tenant": "compile-marketing"},
    )
    assert resp.status_code == 200, resp.text

    assert s3.put_object.call_count == 3
    keys = [call.kwargs["Key"] for call in s3.put_object.call_args_list]
    suffixes = sorted(k.rsplit("/", 1)[1] for k in keys)
    assert suffixes == ["document.pdf", "extract.json", "meta.json"]
    for k in keys:
        assert k.startswith("codex/test/tenant=compile-marketing/dt=")
        assert "/sha256=" in k

    meta_call = next(c for c in s3.put_object.call_args_list if c.kwargs["Key"].endswith("meta.json"))
    meta = json.loads(meta_call.kwargs["Body"].decode())
    assert meta["tenant"] == "compile-marketing"
    assert meta["consent_source"] == "form"
    assert meta["retention_window_days"] == 90
    assert len(meta["sha256"]) == 64


def test_extract_opt_out_writes_nothing(
    client_with_retention: TestClient, s3: MagicMock
) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client_with_retention.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        data={"retain_for_training": "false"},
    )
    assert resp.status_code == 200
    assert s3.put_object.call_count == 0


def test_extract_no_flag_writes_nothing(
    client_with_retention: TestClient, s3: MagicMock
) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client_with_retention.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    assert s3.put_object.call_count == 0


def test_extract_header_only_opt_in(
    client_with_retention: TestClient, s3: MagicMock
) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client_with_retention.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        headers={"X-Compile-Retain-For-Training": "true"},
    )
    assert resp.status_code == 200
    assert s3.put_object.call_count == 3
    meta_call = next(
        c for c in s3.put_object.call_args_list if c.kwargs["Key"].endswith("meta.json")
    )
    meta = json.loads(meta_call.kwargs["Body"].decode())
    assert meta["consent_source"] == "header"


def test_retention_delete_returns_count(
    client_with_retention: TestClient, s3: MagicMock
) -> None:
    sha = "a" * 64
    s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": f"codex/test/tenant=t/dt=2026-05-11/sha256={sha}/document.pdf"},
            {"Key": f"codex/test/tenant=t/dt=2026-05-11/sha256={sha}/extract.json"},
            {"Key": f"codex/test/tenant=t/dt=2026-05-11/sha256={sha}/meta.json"},
            {"Key": "codex/test/tenant=t/dt=2026-05-11/sha256=other/document.pdf"},
        ],
        "IsTruncated": False,
    }
    resp = client_with_retention.post(
        "/v1/retention/delete", json={"sha256": sha}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"sha256": sha, "deleted": 3}
    s3.delete_objects.assert_called_once()
    deleted_keys = [obj["Key"] for obj in s3.delete_objects.call_args.kwargs["Delete"]["Objects"]]
    assert all(f"sha256={sha}" in k for k in deleted_keys)


def test_retention_delete_503_when_unconfigured(
    client_without_retention: TestClient,
) -> None:
    resp = client_without_retention.post(
        "/v1/retention/delete", json={"sha256": "a" * 64}
    )
    assert resp.status_code == 503


def test_retention_delete_400_on_bad_sha(
    client_with_retention: TestClient,
) -> None:
    resp = client_with_retention.post(
        "/v1/retention/delete", json={"sha256": "not-a-sha"}
    )
    assert resp.status_code == 400
