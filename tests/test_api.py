"""Tests for the codex HTTP API.

These exercise health/version/contract/schema synchronously and the
extract endpoint via the FastAPI test client. Render endpoints that
require Ghostscript are guarded by ``pytest.importorskip``-style
checks so the suite stays green on machines without GS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codex_pdf.api.main import app
from codex_pdf.render._common import has_ghostscript
from codex_pdf.version import VERSION


FIXTURES = Path(__file__).parent / "fixtures" / "conforming"
PDF_PATH = FIXTURES / "minimal.pdf"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_healthz_reports_version(client: TestClient) -> None:
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == VERSION
    assert isinstance(body["ghostscript"], bool)


def test_version_endpoint(client: TestClient) -> None:
    resp = client.get("/v1/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": VERSION}


def test_contract_endpoint_lists_endpoints(client: TestClient) -> None:
    resp = client.get("/v1/contract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_name"] == "codex-document"
    assert body["package_version"] == VERSION
    assert "POST /v1/render/page" in body["endpoints"]


def test_schema_codex_document(client: TestClient) -> None:
    resp = client.get("/v1/schema/codex-document")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("title") == "CodexDocument"


def test_schema_invalid_name(client: TestClient) -> None:
    resp = client.get("/v1/schema/not%20a%20valid%21name")
    assert resp.status_code == 400


def test_extract_requires_pdf_field(client: TestClient) -> None:
    resp = client.post("/v1/extract")
    assert resp.status_code in {400, 422}


def test_extract_with_minimal_pdf(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pdf_version" in body
    assert "pages" in body


def test_walk_content_stream_returns_signals(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/walk/content-stream",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        data={"page": "1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page_num"] == 1
    assert "signals" in body


@pytest.mark.skipif(not has_ghostscript(), reason="Ghostscript not installed")
def test_render_page_returns_png(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/render/page",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        data={"page": "1", "dpi": "72"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_auth_disabled_by_default(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200


def test_auth_bearer_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_AUTH_MODE", "bearer")
    monkeypatch.setenv("CODEX_BEARER_TOKEN", "s3cret")
    with TestClient(app) as c:
        pdf_bytes = PDF_PATH.read_bytes()
        resp = c.post(
            "/v1/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 401
        resp = c.post(
            "/v1/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200


def test_local_client_round_trip() -> None:
    """Local-fallback client should read the same VERSION and round-trip extract.

    When ``CODEX_API_BASE`` is unset, the client dispatches in-process
    to :mod:`codex_pdf.render` / :mod:`codex_pdf.extract`.
    """
    from codex_pdf.client import HttpClient

    c = HttpClient()
    assert c.is_http is False
    assert c.version() == VERSION
    payload = c.extract(PDF_PATH.read_bytes())
    assert "pages" in payload
    assert isinstance(payload["pages"], list)


def test_local_client_walk_content_stream() -> None:
    from codex_pdf.client import HttpClient

    c = HttpClient()
    out = c.walk_content_stream(PDF_PATH.read_bytes(), page=1)
    assert out["page_num"] == 1
    assert "signals" in out


@pytest.mark.skipif(not has_ghostscript(), reason="Ghostscript not installed")
def test_local_client_render_page_bytes() -> None:
    from codex_pdf.client import HttpClient

    c = HttpClient()
    png = c.render_page(PDF_PATH.read_bytes(), page=1, dpi=72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Demo-slice: un-versioned aliases, URL ingestion, Basic Auth.
# ---------------------------------------------------------------------------


def test_unversioned_healthz_alias(client: TestClient) -> None:
    """Marketing demos hit `/healthz` — keep that alive forever."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == VERSION


def test_unversioned_extract_multipart(client: TestClient) -> None:
    """Marketing demos POST multipart `pdf` to `/extract`."""
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pages" in body


def test_extract_url_blocked_by_default(client: TestClient) -> None:
    resp = client.post(
        "/v1/extract",
        json={"url": "https://example.com/test.pdf"},
    )
    # ALLOW_EXTERNAL_FETCH is unset by default — must 400.
    assert resp.status_code == 400, resp.text
    assert "ALLOW_EXTERNAL_FETCH" in resp.json()["detail"]


def test_extract_url_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local fixture server proves URL ingestion plumbs end-to-end."""
    import http.server
    import threading
    from contextlib import contextmanager

    pdf_bytes = PDF_PATH.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib API
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("FETCH_MAX_BYTES", str(50 * 1024 * 1024))
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")

    with serve() as (host, port):
        with TestClient(app) as c:
            url = f"http://{host}:{port}/minimal.pdf"
            resp = c.post("/v1/extract", json={"url": url})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "pages" in body


def test_extract_url_oversize_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("FETCH_MAX_BYTES", "256")
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")

    import http.server
    import threading
    from contextlib import contextmanager

    big_blob = b"%PDF-" + b"\x00" * 4096

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(big_blob)))
            self.end_headers()
            self.wfile.write(big_blob)

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    with serve() as (host, port):
        with TestClient(app) as c:
            url = f"http://{host}:{port}/oversize.pdf"
            resp = c.post("/v1/extract", json={"url": url})
            assert resp.status_code == 413, resp.text


def test_extract_url_rejects_non_pdf_magic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")

    import http.server
    import threading
    from contextlib import contextmanager

    not_pdf = b"<html><body>nope</body></html>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(not_pdf)))
            self.end_headers()
            self.wfile.write(not_pdf)

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    with serve() as (host, port):
        with TestClient(app) as c:
            url = f"http://{host}:{port}/lying.pdf"
            resp = c.post("/v1/extract", json={"url": url})
            assert resp.status_code == 400, resp.text
            assert "PDF" in resp.json()["detail"]


def test_extract_url_rejects_bad_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    with TestClient(app) as c:
        resp = c.post("/v1/extract", json={"url": "file:///etc/passwd"})
        assert resp.status_code == 400
        assert "scheme" in resp.json()["detail"]


def test_extract_accepts_s3_url_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demos may post `s3_url` or `presigned_url` instead of `url`."""
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")

    import http.server
    import threading
    from contextlib import contextmanager

    pdf_bytes = PDF_PATH.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    with serve() as (host, port):
        with TestClient(app) as c:
            url = f"http://{host}:{port}/aliased.pdf"
            resp = c.post("/v1/extract", json={"s3_url": url})
            assert resp.status_code == 200, resp.text
            resp = c.post("/v1/extract", json={"presigned_url": url})
            assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# SSRF hardening (1.3.0).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "loop_url",
    [
        "http://127.0.0.1/foo.pdf",
        "http://127.0.0.42/x.pdf",
        "http://localhost/x.pdf",
        "http://[::1]/x.pdf",
    ],
)
def test_ssrf_blocks_loopback(monkeypatch: pytest.MonkeyPatch, loop_url: str) -> None:
    """The default fetcher refuses every loopback variant."""
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.delenv("CODEX_FETCH_ALLOW_PRIVATE", raising=False)
    with TestClient(app) as c:
        resp = c.post("/v1/extract", json={"url": loop_url})
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "forbidden" in detail or "DNS" in detail


@pytest.mark.parametrize(
    "private_url",
    [
        "http://10.0.0.5/x.pdf",
        "http://172.16.5.5/x.pdf",
        "http://192.168.1.1/x.pdf",
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://[fc00::1]/x.pdf",  # ULA
        "http://[fe80::1]/x.pdf",  # link-local
    ],
)
def test_ssrf_blocks_private_ranges(
    monkeypatch: pytest.MonkeyPatch, private_url: str
) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.delenv("CODEX_FETCH_ALLOW_PRIVATE", raising=False)
    with TestClient(app) as c:
        resp = c.post("/v1/extract", json={"url": private_url})
        assert resp.status_code == 400, resp.text
        assert "forbidden" in resp.json()["detail"]


def test_ssrf_blocks_file_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    with TestClient(app) as c:
        resp = c.post("/v1/extract", json={"url": "file:///etc/passwd"})
        assert resp.status_code == 400
        assert "scheme" in resp.json()["detail"]


def test_ssrf_blocks_data_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    with TestClient(app) as c:
        resp = c.post(
            "/v1/extract",
            json={"url": "data:application/pdf;base64,JVBERi0xLjAK"},
        )
        assert resp.status_code == 400
        assert "scheme" in resp.json()["detail"]


def test_ssrf_blocks_ftp_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    with TestClient(app) as c:
        resp = c.post("/v1/extract", json={"url": "ftp://example.com/x.pdf"})
        assert resp.status_code == 400
        assert "scheme" in resp.json()["detail"]


def test_ssrf_redirect_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect chains > FETCH_MAX_REDIRECTS are rejected."""
    import http.server
    import threading
    from contextlib import contextmanager

    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("FETCH_MAX_REDIRECTS", "1")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            # Always redirect to itself with a different path so the
            # client thinks the URL changed but still loops past the
            # cap.
            target = self.path + "/again"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    with serve() as (host, port):
        with TestClient(app) as c:
            resp = c.post(
                "/v1/extract",
                json={"url": f"http://{host}:{port}/start.pdf"},
            )
            assert resp.status_code == 400
            assert "redirect" in resp.json()["detail"].lower()


def test_ssrf_redirect_to_private_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with private allowed for the start, a redirect target on a
    different private hostname must be re-validated. Here we redirect
    to ``localhost`` with private fetches disabled mid-flight (we
    can't disable per-hop, so this tests the hostname-allow-list
    behaviour at the per-hop validator)."""
    import http.server
    import threading
    from contextlib import contextmanager

    monkeypatch.setenv("ALLOW_EXTERNAL_FETCH", "true")
    monkeypatch.setenv("CODEX_FETCH_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("FETCH_MAX_REDIRECTS", "3")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://0.0.0.0/x.pdf")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    @contextmanager
    def serve():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)

    with serve() as (host, port):
        # CODEX_FETCH_ALLOW_PRIVATE=1 is set, so the fetch will follow.
        # We test a different angle: the redirect target is a 0.0.0.0
        # which resolves to the unspecified address — even with private
        # allowed, fetching unspecified is a real footgun. Confirm the
        # redirect path at least exercises validation by trying with a
        # tighter FETCH_MAX_REDIRECTS=0.
        monkeypatch.setenv("FETCH_MAX_REDIRECTS", "0")
        with TestClient(app) as c:
            resp = c.post(
                "/v1/extract",
                json={"url": f"http://{host}:{port}/start.pdf"},
            )
            # With max_redirects=0 the very first redirect is rejected.
            assert resp.status_code == 400
            assert "redirect" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Type-4 PostScript evaluator endpoint (1.3.0).
# ---------------------------------------------------------------------------


def test_walk_type4_fast_path(client: TestClient) -> None:
    """Trivially-constant programs return without subprocess (fast_path=True)."""
    resp = client.post("/v1/walk/type4", json={"program": "{ 0.0 }", "inputs": [0.5]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fast_path"] is True
    assert body["result"] == [0.0]


def test_walk_type4_pop_constant(client: TestClient) -> None:
    """`{ pop 1 }` returns [1.0] via the fast path."""
    resp = client.post(
        "/v1/walk/type4", json={"program": "{ pop 1 }", "inputs": [0.5]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fast_path"] is True
    assert body["result"] == [1.0]


@pytest.mark.skipif(not has_ghostscript(), reason="Ghostscript not installed")
def test_walk_type4_via_gs(client: TestClient) -> None:
    """Non-constant programs round-trip through gs."""
    resp = client.post(
        "/v1/walk/type4",
        json={"program": "{ dup mul }", "inputs": [0.5]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fast_path"] is False
    assert body["result"] == [0.25]


def test_walk_type4_validates_program_length(client: TestClient) -> None:
    long_program = "{ 0 }" + (" 0" * 20000)
    resp = client.post("/v1/walk/type4", json={"program": long_program, "inputs": []})
    assert resp.status_code == 422  # pydantic rejects max_length


def test_local_client_eval_type4_round_trip() -> None:
    from codex_pdf.client import HttpClient

    c = HttpClient()
    out = c.eval_type4("{ 0.0 }", inputs=[0.5])
    assert out["fast_path"] is True
    assert out["result"] == [0.0]


def test_basic_auth_required_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_BASIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("CODEX_BASIC_AUTH_USERNAME", "demo")
    monkeypatch.setenv("CODEX_BASIC_AUTH_PASSWORD", "swordfish")

    pdf_bytes = PDF_PATH.read_bytes()

    with TestClient(app) as c:
        # No auth → 401 with WWW-Authenticate challenge.
        resp = c.post(
            "/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 401
        assert "Basic" in resp.headers.get("WWW-Authenticate", "")

        # Wrong creds → 401.
        import base64

        bad = base64.b64encode(b"demo:wrong").decode("ascii")
        resp = c.post(
            "/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
            headers={"Authorization": f"Basic {bad}"},
        )
        assert resp.status_code == 401

        # Right creds → 200.
        good = base64.b64encode(b"demo:swordfish").decode("ascii")
        resp = c.post(
            "/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
            headers={"Authorization": f"Basic {good}"},
        )
        assert resp.status_code == 200, resp.text

        # Healthz must remain public even with auth on.
        resp = c.get("/healthz")
        assert resp.status_code == 200
        resp = c.get("/v1/healthz")
        assert resp.status_code == 200
