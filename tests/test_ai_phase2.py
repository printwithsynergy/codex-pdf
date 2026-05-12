"""Phase 2 (1.14.0) operational contract tests.

Pins per-tenant AI entitlements (CODEX_AI_TENANTS_ALLOWLIST /
DENYLIST) + the tenant_excluded warning emission.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from codex_pdf.ai.context import build_context
from codex_pdf.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

_PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000052 00000 n\n0000000098 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n149\n%%EOF\n"
)


def test_build_context_disabled_when_operator_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_AI_ENABLED", raising=False)
    ctx = build_context(caller_skipped=False, tenant="acme")
    assert ctx.status == "disabled"


def test_build_context_tenant_excluded_via_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_AI_TENANTS_ALLOWLIST", "pilot1,pilot2")
    ctx = build_context(caller_skipped=False, tenant="acme")
    assert ctx.status == "tenant_excluded"


def test_build_context_tenant_passes_via_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_AI_TENANTS_ALLOWLIST", "acme,beta")
    ctx = build_context(caller_skipped=False, tenant="acme")
    # acme is in allowlist; without anthropic SDK installed we fall
    # through to missing_credentials. The point of this test is that
    # we get PAST tenant_excluded.
    assert ctx.status in {"enabled", "missing_credentials"}


def test_build_context_tenant_excluded_via_denylist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CODEX_AI_TENANTS_ALLOWLIST", raising=False)
    monkeypatch.setenv("CODEX_AI_TENANTS_DENYLIST", "spammer,abuser")
    ctx = build_context(caller_skipped=False, tenant="spammer")
    assert ctx.status == "tenant_excluded"


def test_extract_emits_ai_tenant_excluded_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator opted in but the tenant isn't in the allowlist.
    Codex emits ``ai_tenant_excluded`` so consumers know the empty
    signals are policy, not a missing call."""
    monkeypatch.setenv("CODEX_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_AI_TENANTS_ALLOWLIST", "pilot")
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", _PDF_BYTES, "application/pdf")},
        headers={"X-Codex-Tenant": "acme"},
    )
    assert resp.status_code == 200
    body = resp.json()
    warnings = body.get("extraction_warnings") or []
    codes = {w.get("code") for w in warnings if isinstance(w, dict)}
    assert "ai_tenant_excluded" in codes, codes
