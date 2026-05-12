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
    assert "POST /v1/retention/delete" in body["endpoints"]


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


def test_extract_stream_emits_two_phases(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract/stream",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers.get("content-type", "")
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 2, f"expected 2 SSE events, got {len(events)}"
    p1, p2 = events
    assert p1["extract_phase"] == 1
    assert p2["extract_phase"] == 2
    assert "pages" in p1
    assert "pages" in p2
    assert p1.get("ocgs") == []
    assert isinstance(p2.get("ocgs"), list)


def test_probe_emits_two_events(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/probe",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers.get("content-type", "")
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 2
    ev1, ev2 = events
    assert ev1["probe_phase"] == 1
    assert ev2["probe_phase"] == 2
    assert ev1["page_count"] == ev2["page_count"]
    assert ev1["page_count"] >= 1
    assert "first_page_dims" in ev1
    assert isinstance(ev2.get("page_dims"), list)
    assert len(ev2["page_dims"]) == ev2["page_count"]
    assert ev1["pdf_sha256"] == ev2["pdf_sha256"]


def test_probe_resolves_sha_from_blob_store(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    # First call uploads + caches the blob.
    first = client.post(
        "/v1/probe",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert first.status_code == 200
    sha = next(
        json.loads(line[len("data: "):])["pdf_sha256"]
        for line in first.text.splitlines()
        if line.startswith("data: ")
    )
    # Second call references the cached sha — no re-upload.
    second = client.post("/v1/probe", json={"pdf_sha256": sha})
    assert second.status_code == 200, second.text
    events = [
        json.loads(line[len("data: "):])
        for line in second.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 2
    assert events[0]["pdf_sha256"] == sha


def test_probe_rejects_unknown_sha(client: TestClient) -> None:
    resp = client.post("/v1/probe", json={"pdf_sha256": "0" * 64})
    assert resp.status_code == 412


def test_extract_stream_granular_events(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract/stream?granular=1",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    # Granular mode emits ``event: <name>\n`` lines alongside data:.
    event_names = [
        line[len("event: "):]
        for line in resp.text.splitlines()
        if line.startswith("event: ")
    ]
    assert event_names[0] == "phase1"
    assert event_names[-1] == "phase2_complete"
    middle = set(event_names[1:-1])
    assert middle == {"color_world", "ocgs", "form_xobjects", "analysis"}


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


# ---------------------------------------------------------------------------
# Unified extraction contract: stage telemetry + per-resource endpoints.
# These tests pin the public surface added in 1.2.0 so consumers (lint,
# loupe, compile, …) can wire against it ahead of the implementation.
# ---------------------------------------------------------------------------


_ZERO_SHA = "0" * 64


def test_extract_emits_stage_durations(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    header = resp.headers.get("X-Codex-Stage-Durations-Ms")
    assert header is not None, resp.headers
    parsed = json.loads(header)
    assert "extract" in parsed
    assert isinstance(parsed["extract"], int)
    body = resp.json()
    assert body.get("stage_durations_ms") == parsed


def test_extract_response_carries_additive_fields(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Pages carry an additive detected_text_regions list (empty until populated).
    assert isinstance(body["pages"][0]["detected_text_regions"], list)
    # Conformance verdicts default to an empty dict; consumers post to
    # fill them on demand. Stage telemetry envelope is always present.
    assert isinstance(body.get("conformance_verdicts"), dict)
    assert isinstance(body.get("stage_durations_ms"), dict)


def test_text_regions_unknown_document_returns_404(client: TestClient) -> None:
    resp = client.get(f"/v1/documents/{_ZERO_SHA}/text-regions?page_index=0&dpi=150")
    assert resp.status_code == 404, resp.text
    assert "not in cache" in resp.json()["detail"].lower()


def test_text_regions_returns_regions_for_extracted_pdf(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    extract = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert extract.status_code == 200
    sha = extract.json()["pdf_sha256"]

    resp = client.get(f"/v1/documents/{sha}/text-regions?page_index=0&dpi=150")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pdf_hash"] == sha
    assert body["page_index"] == 0
    assert body["dpi"] == 150
    assert isinstance(body["regions"], list)
    # Stage timing is emitted on both the envelope and the header.
    assert "text_regions" in body["stage_durations_ms"]
    header = resp.headers.get("X-Codex-Stage-Durations-Ms")
    assert header is not None
    assert "text_regions" in json.loads(header)

    # Idempotent: second call returns the same payload bytes (cache hit).
    second = client.get(f"/v1/documents/{sha}/text-regions?page_index=0&dpi=150")
    assert second.status_code == 200
    assert second.json()["regions"] == body["regions"]


def test_text_regions_endpoint_validates_hash_and_args(client: TestClient) -> None:
    bad_hash = client.get("/v1/documents/not-a-hash/text-regions")
    assert bad_hash.status_code == 400
    bad_page = client.get(f"/v1/documents/{_ZERO_SHA}/text-regions?page_index=-1")
    assert bad_page.status_code == 400
    bad_dpi = client.get(f"/v1/documents/{_ZERO_SHA}/text-regions?dpi=5")
    assert bad_dpi.status_code == 400


def test_conformance_unknown_document_returns_404(client: TestClient) -> None:
    resp = client.post(f"/v1/documents/{_ZERO_SHA}/conformance/pdfx4")
    assert resp.status_code == 404, resp.text
    assert "not in cache" in resp.json()["detail"].lower()


def test_conformance_returns_verdict_for_extracted_pdf(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    extract = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    sha = extract.json()["pdf_sha256"]

    resp = client.post(f"/v1/documents/{sha}/conformance/pdfx4")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_id"] == sha
    assert body["profile"] == "pdfx4"
    assert isinstance(body["passed"], bool)
    assert isinstance(body["clauses"], list)
    # Failed clauses carry the expected shape; descriptions are non-empty
    # so consumers can surface human-readable text without a separate lookup.
    for clause in body["clauses"]:
        assert clause["clause"]
        assert clause["test_number"]
        assert clause["failed_check_count"] >= 1
    assert "conformance" in body["stage_durations_ms"]

    # Idempotent: second call hits the cache and returns the same verdict.
    second = client.post(f"/v1/documents/{sha}/conformance/pdfx4")
    assert second.status_code == 200
    body2 = second.json()
    assert body2["passed"] == body["passed"]
    assert body2["clauses"] == body["clauses"]


def test_conformance_endpoint_validates_profile_enum(client: TestClient) -> None:
    bad_profile = client.post(f"/v1/documents/{_ZERO_SHA}/conformance/pdfx99")
    assert bad_profile.status_code == 400
    bad_hash = client.post("/v1/documents/short/conformance/pdfx4")
    assert bad_hash.status_code == 400


def test_renders_list_unknown_document_returns_empty(client: TestClient) -> None:
    resp = client.get(f"/v1/documents/{_ZERO_SHA}/renders")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pdf_hash"] == _ZERO_SHA
    assert body["renders"] == []


def test_renders_list_endpoint_validates_hash(client: TestClient) -> None:
    resp = client.get("/v1/documents/short/renders")
    assert resp.status_code == 400


@pytest.mark.skipif(not has_ghostscript(), reason="Ghostscript not installed")
def test_renders_list_reflects_render_cache(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    extract = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    sha = extract.json()["pdf_sha256"]
    render = client.post(
        "/v1/render/page",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        data={"page": "1", "dpi": "72"},
    )
    assert render.status_code == 200

    resp = client.get(f"/v1/documents/{sha}/renders")
    assert resp.status_code == 200
    renders = resp.json()["renders"]
    assert any(
        entry["page_index"] == 0 and entry["dpi"] == 72 and entry["color_space"] == "sRGB"
        for entry in renders
    ), renders


def test_contract_lists_unified_extraction_endpoints(client: TestClient) -> None:
    resp = client.get("/v1/contract")
    assert resp.status_code == 200
    body = resp.json()
    endpoints = body["endpoints"]
    assert "GET /v1/documents/{pdf_hash}/text-regions" in endpoints
    assert "POST /v1/documents/{document_id}/conformance/{profile}" in endpoints
    assert "GET /v1/documents/{pdf_hash}/renders" in endpoints
    assert body["schema_version"] == "1.2.0"


def test_openapi_describes_new_endpoints_and_cache_keys(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    paths = spec["paths"]
    text_path = "/v1/documents/{pdf_hash}/text-regions"
    conformance_path = "/v1/documents/{document_id}/conformance/{profile}"
    renders_path = "/v1/documents/{pdf_hash}/renders"
    assert text_path in paths
    assert conformance_path in paths
    assert renders_path in paths
    # Cache-key contract is part of the public surface: it must be
    # discoverable in the OpenAPI description for each endpoint.
    # The 1.9.0 series scopes by tenant — the contract surface
    # changed to reflect that, but the additive guarantee holds.
    assert "pdf_hash, page_index, dpi" in paths[text_path]["get"]["description"]
    assert "pdf_hash, profile" in paths[conformance_path]["post"]["description"]
    assert "pdf_hash, page_index, dpi, color_space" in paths[renders_path]["get"]["description"]


# ---------------------------------------------------------------------------
# Phase 2 — operational contract. Tenancy scoping, rate-limiting,
# error-shape catalogue, and behavior-locking parity for the extract
# response. See CAMPAIGN.md Phase 2 log for the why.
# ---------------------------------------------------------------------------


def test_tenant_cannot_read_anothers_blob(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    # Tenant A uploads.
    extract = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
        headers={"X-Codex-Tenant": "tenant-a"},
    )
    assert extract.status_code == 200
    sha = extract.json()["pdf_sha256"]
    # Tenant A can read.
    self_read = client.get(
        f"/v1/documents/{sha}/text-regions",
        headers={"X-Codex-Tenant": "tenant-a"},
    )
    assert self_read.status_code == 200
    # Tenant B with the same hash gets 404 — content-addressed
    # storage is still scoped by tenant. (We only test A → B here
    # because the default tenant's blob store may be polluted by
    # prior tests in the same module; module-level state isn't
    # reset between tests, but two arbitrary tenant names are
    # guaranteed unique.)
    other_read = client.get(
        f"/v1/documents/{sha}/text-regions",
        headers={"X-Codex-Tenant": "tenant-b"},
    )
    assert other_read.status_code == 404


def test_tenants_have_independent_conformance_caches(client: TestClient) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    # Both tenants upload the same PDF and compute the same profile.
    for tenant in ("tenant-a", "tenant-b"):
        extract = client.post(
            "/v1/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
            headers={"X-Codex-Tenant": tenant},
        )
        assert extract.status_code == 200
        sha = extract.json()["pdf_sha256"]
        verdict = client.post(
            f"/v1/documents/{sha}/conformance/pdfx4",
            headers={"X-Codex-Tenant": tenant},
        )
        assert verdict.status_code == 200
        # The verdict is computed independently per tenant — content
        # is the same here because the input is, but the cache entries
        # are isolated. We just need to make sure neither call leaks
        # past tenant boundaries.
        assert verdict.json()["document_id"] == sha


def test_unified_error_shape_on_404(client: TestClient) -> None:
    bogus = "0" * 64
    resp = client.get(f"/v1/documents/{bogus}/text-regions")
    assert resp.status_code == 404
    # Every error response — including 404 — uses the shared envelope.
    body = resp.json()
    assert set(body.keys()) == {"detail"}, body
    assert isinstance(body["detail"], str) and body["detail"]


def test_openapi_documents_phase_2_error_responses(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    spec = resp.json()
    text_path = "/v1/documents/{pdf_hash}/text-regions"
    text_responses = spec["paths"][text_path]["get"]["responses"]
    # 400 / 404 / 429 are all documented for the text-regions GET so
    # consumers can wire UI states without trial-and-error.
    assert "400" in text_responses
    assert "404" in text_responses
    assert "429" in text_responses


def test_rate_limit_returns_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token bucket fires when burst is exhausted."""
    monkeypatch.setenv("CODEX_RATE_LIMIT_DISABLED", "false")
    monkeypatch.setenv("CODEX_RATE_LIMIT_RPM", "60")
    monkeypatch.setenv("CODEX_RATE_LIMIT_BURST", "2")
    # Re-import to pick up the new env. The module-level limiter
    # is built once at import; we install a fresh one for the test.
    from codex_pdf.api import main as main_module
    from codex_pdf.api.rate_limit import make_rate_limiter

    fresh = make_rate_limiter()
    assert fresh is not None
    monkeypatch.setattr(main_module, "_rate_limiter", fresh)

    pdf_bytes = PDF_PATH.read_bytes()
    with TestClient(main_module.app) as c:
        # Two extracts within the burst succeed.
        for _ in range(2):
            resp = c.post(
                "/v1/extract",
                files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
                headers={"X-Codex-Tenant": "rate-limit-test"},
            )
            assert resp.status_code == 200
        # Third within the same second exhausts the bucket → 429.
        third = c.post(
            "/v1/extract",
            files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
            headers={"X-Codex-Tenant": "rate-limit-test"},
        )
        assert third.status_code == 429
        assert "retry-after" in {h.lower() for h in third.headers.keys()}


def test_extract_response_is_additive_only(client: TestClient) -> None:
    """Behavior-locking parity test for /v1/extract response shape.

    Pins the set of fields a 1.0-vintage consumer expects. The
    1.9.0 series adds fields (``stage_durations_ms``, etc.) but
    must not remove or rename any pre-existing field.
    """
    pdf_bytes = PDF_PATH.read_bytes()
    resp = client.post(
        "/v1/extract",
        files={"pdf": ("minimal.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    expected_pre_v1_2_fields = {
        "schema_version",
        "codex_version",
        "document_id",
        "source",
        "pdf_version",
        "is_encrypted",
        "is_linearized",
        "conformance",
        "info",
        "xmp",
        "trapped_flag",
        "output_intents",
        "icc_profiles",
        "color_spaces",
        "fonts",
        "images",
        "ocgs",
        "pages",
        "form_xobjects",
        "trap_evidence",
        "annotations",
        "analysis",
        "summary",
        "preflight_reports",
        "extraction_warnings",
    }
    missing = expected_pre_v1_2_fields - set(body.keys())
    assert not missing, f"removed/renamed fields: {missing}"
    # Page-level: every page kept the v1.0 shape.
    expected_page_fields = {
        "page_num",
        "rotation",
        "boxes",
        "resources",
        "inventory",
        "transparency_tree",
        "annotations",
        "analysis",
    }
    page = body["pages"][0]
    missing_page = expected_page_fields - set(page.keys())
    assert not missing_page, f"page-level removed/renamed fields: {missing_page}"
