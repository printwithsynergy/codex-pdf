"""Codex Python client.

Single class :class:`HttpClient` exposing the codex surface. HTTP
mode uses ``urllib.request`` with exponential-backoff retries (no
runtime dep on requests/httpx) and streams binary responses. Local
fallback skips HTTP entirely and dispatches to in-process functions
in :mod:`codex_pdf.render` / :mod:`codex_pdf.extract`.

Both modes share the same return shapes so callers don't branch.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger(__name__)


class CodexClientError(RuntimeError):
    """Raised when an HTTP call fails after exhausting retries.

    Carries ``status`` (HTTP status, or ``-1`` on transport failure)
    and ``body`` (truncated response body) so callers can render a
    useful error to a frontend.
    """

    def __init__(self, message: str, *, status: int = -1, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ColorSample:
    x: float
    y: float
    dpi: int
    rgb: tuple[int, int, int]
    hex: str


@dataclass(frozen=True)
class DensitometerSample:
    x: float
    y: float
    dpi: int
    channels: list[dict[str, Any]]
    tac: float
    tac_limit: float
    limit_exceeded: bool


@dataclass(frozen=True)
class SeparationsResult:
    page_num: int
    dpi: int
    channels: list[dict[str, Any]]


@dataclass(frozen=True)
class HeatmapResult:
    png: bytes
    runs: list[dict[str, Any]]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_multipart(
    pdf_bytes: bytes,
    *,
    filename: str = "input.pdf",
    fields: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    boundary = "----codex" + secrets.token_hex(8)
    lines: list[bytes] = []
    fields = fields or {}
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, list):
            v = ",".join(str(x) for x in v)
        lines.append(f"--{boundary}".encode("ascii"))
        lines.append(
            f'Content-Disposition: form-data; name="{k}"'.encode("ascii")
        )
        lines.append(b"")
        lines.append(str(v).encode("utf-8"))
    lines.append(f"--{boundary}".encode("ascii"))
    lines.append(
        f'Content-Disposition: form-data; name="pdf"; filename="{filename}"'.encode(
            "ascii"
        )
    )
    ct = mimetypes.guess_type(filename)[0] or "application/pdf"
    lines.append(f"Content-Type: {ct}".encode("ascii"))
    lines.append(b"")
    lines.append(pdf_bytes)
    lines.append(f"--{boundary}--".encode("ascii"))
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, boundary


class HttpClient:
    """Codex client. Pass nothing to read config from environment."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        bearer_token: str | None = None,
        api_key: str | None = None,
        internal_token: str | None = None,
        timeout_ms: int | None = None,
        max_retries: int = 3,
        local_fallback: bool | None = None,
    ) -> None:
        self.base_url = base_url or os.environ.get("CODEX_API_BASE")
        self.bearer_token = bearer_token or os.environ.get("CODEX_BEARER_TOKEN")
        self.api_key = api_key or os.environ.get("CODEX_API_KEY")
        self.internal_token = internal_token or os.environ.get("CODEX_INTERNAL_TOKEN")
        env_timeout = os.environ.get("CODEX_TIMEOUT_MS")
        timeout_value = timeout_ms if timeout_ms is not None else (
            int(env_timeout) if env_timeout else 60000
        )
        self.timeout_seconds = max(1.0, timeout_value / 1000.0)
        self.max_retries = max_retries
        if local_fallback is None:
            local_fallback = _bool_env("CODEX_LOCAL_FALLBACK", default=True)
        self.local_fallback = local_fallback

    # -----------------------------------------------------------------
    # Mode selection.
    # -----------------------------------------------------------------

    @property
    def is_http(self) -> bool:
        return bool(self.base_url)

    def _require_http_or_local(self) -> str | None:
        if self.is_http:
            return self.base_url
        if self.local_fallback:
            return None
        raise CodexClientError(
            "CODEX_API_BASE not configured and CODEX_LOCAL_FALLBACK disabled",
            status=-1,
        )

    # -----------------------------------------------------------------
    # Low-level HTTP helpers (urllib so we have no third-party dep).
    # -----------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.api_key:
            headers["X-Codex-Key"] = self.api_key
        if self.internal_token:
            headers["X-Codex-Internal"] = self.internal_token
        if extra:
            headers.update(extra)
        return headers

    def _post(
        self,
        path: str,
        *,
        body: bytes,
        content_type: str,
        accept: str = "*/*",
    ) -> tuple[int, bytes, dict[str, str]]:
        assert self.base_url is not None
        url = self.base_url.rstrip("/") + path
        headers = self._headers({"Content-Type": content_type, "Accept": accept})
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urlrequest.Request(url, data=body, headers=headers, method="POST")
            try:
                with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read()
                    return resp.getcode(), raw, dict(resp.headers.items())
            except urlerror.HTTPError as exc:
                if exc.code in {408, 429} or 500 <= exc.code < 600:
                    last_exc = exc
                    backoff = min(2.0 ** attempt, 8.0)
                    logger.warning("codex %s -> %d, retry %d in %.1fs", path, exc.code, attempt, backoff)
                    time.sleep(backoff)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                raise CodexClientError(
                    f"codex {path} -> {exc.code}: {detail}",
                    status=exc.code,
                    body=detail,
                ) from exc
            except Exception as exc:
                last_exc = exc
                backoff = min(2.0 ** attempt, 8.0)
                logger.warning("codex %s transport failure, retry %d in %.1fs", path, attempt, backoff)
                time.sleep(backoff)
        raise CodexClientError(
            f"codex {path} failed after {self.max_retries + 1} attempts: {last_exc}",
            status=-1,
        )

    def _get(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        assert self.base_url is not None
        url = self.base_url.rstrip("/") + path
        req = urlrequest.Request(url, headers=self._headers(), method="GET")
        with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
            return resp.getcode(), resp.read(), dict(resp.headers.items())

    @staticmethod
    def _coerce_bytes(pdf: bytes | BinaryIO | str | os.PathLike[str]) -> bytes:
        if isinstance(pdf, bytes):
            return pdf
        if hasattr(pdf, "read"):
            return pdf.read()  # type: ignore[union-attr]
        with open(pdf, "rb") as fh:
            return fh.read()

    # -----------------------------------------------------------------
    # Public API.
    # -----------------------------------------------------------------

    def healthz(self) -> dict[str, Any]:
        if not self.is_http:
            from codex_pdf.render._common import has_ghostscript
            from codex_pdf.version import VERSION

            return {"status": "ok", "version": VERSION, "ghostscript": has_ghostscript()}
        _status, raw, _headers = self._get("/v1/healthz")
        return json.loads(raw)

    def version(self) -> str:
        if not self.is_http:
            from codex_pdf.version import VERSION

            return VERSION
        _status, raw, _headers = self._get("/v1/version")
        return json.loads(raw).get("version", "unknown")

    def contract(self) -> dict[str, Any]:
        if not self.is_http:
            from codex_pdf.version import VERSION

            return {
                "contract_name": "codex-document",
                "schema_version": "1.0.0",
                "package_version": VERSION,
                "endpoints": [],
            }
        _status, raw, _headers = self._get("/v1/contract")
        return json.loads(raw)

    def extract(self, pdf: bytes | BinaryIO | str | os.PathLike[str]) -> dict[str, Any]:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from pathlib import Path

            from codex_pdf.extract import extract_from_path

            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                doc = extract_from_path(Path(tmp_path))
                return doc.model_dump(mode="json")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        body, boundary = _build_multipart(raw)
        _status, response_body, _headers = self._post(
            "/v1/extract",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="application/json",
        )
        return json.loads(response_body)

    def render_page(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        dpi: int = 300,
        ocg_on: list[int] | None = None,
        ocg_off: list[int] | None = None,
        simulate_overprint: bool = True,
    ) -> bytes:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render.page import render_page

            return render_page(
                raw,
                page,
                dpi=dpi,
                ocg_on=ocg_on,
                ocg_off=ocg_off,
                simulate_overprint=simulate_overprint,
            )
        body, boundary = _build_multipart(
            raw,
            fields={
                "page": page,
                "dpi": dpi,
                "ocg_on": ocg_on or [],
                "ocg_off": ocg_off or [],
                "simulate_overprint": "true" if simulate_overprint else "false",
            },
        )
        _status, response_body, _headers = self._post(
            "/v1/render/page",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="image/png",
        )
        return response_body

    def render_separations(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        dpi: int = 150,
    ) -> SeparationsResult:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render.separations import render_separations

            res = render_separations(raw, page, dpi=dpi)
            return SeparationsResult(
                page_num=res["page_num"],
                dpi=res["dpi"],
                channels=[
                    {"name": c["name"], "type": c["type"], "png": c["png"]}
                    for c in res["channels"]
                ],
            )
        body, boundary = _build_multipart(raw, fields={"page": page, "dpi": dpi})
        _status, response_body, _headers = self._post(
            "/v1/render/separations",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="application/json",
        )
        payload = json.loads(response_body)
        channels = []
        for ch in payload.get("channels", []):
            png_b64 = ch.get("png_b64", "")
            channels.append(
                {
                    "name": ch["name"],
                    "type": ch["type"],
                    "png": base64.b64decode(png_b64) if png_b64 else b"",
                }
            )
        return SeparationsResult(
            page_num=int(payload.get("page_num", page)),
            dpi=int(payload.get("dpi", dpi)),
            channels=channels,
        )

    def render_heatmap(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        dpi: int = 150,
        tac_limit: float = 300,
    ) -> HeatmapResult:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render.separations import render_heatmap

            res = render_heatmap(raw, page, dpi=dpi, tac_limit=tac_limit)
            return HeatmapResult(png=res["png"], runs=list(res["runs"]))
        body, boundary = _build_multipart(
            raw, fields={"page": page, "dpi": dpi, "tac_limit": tac_limit}
        )
        _status, response_body, headers = self._post(
            "/v1/render/heatmap",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="image/png",
        )
        runs_json = headers.get("X-Codex-Tac-Runs") or headers.get("x-codex-tac-runs") or "[]"
        try:
            runs = json.loads(runs_json)
        except json.JSONDecodeError:
            runs = []
        return HeatmapResult(png=response_body, runs=runs)

    def render_layer(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        layer_index: int,
        all_layer_indices: list[int],
        dpi: int = 150,
    ) -> bytes:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render.layer import render_layer

            return render_layer(
                raw,
                page,
                layer_index=layer_index,
                all_layer_indices=all_layer_indices,
                dpi=dpi,
            )
        body, boundary = _build_multipart(
            raw,
            fields={
                "page": page,
                "layer_index": layer_index,
                "all_layer_indices": all_layer_indices,
                "dpi": dpi,
            },
        )
        _status, response_body, _headers = self._post(
            "/v1/render/layer",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="image/png",
        )
        return response_body

    def sample_color(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        x: float,
        y: float,
        page_w: float | None = None,
        page_h: float | None = None,
        dpi: int = 300,
    ) -> ColorSample:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render._common import get_page_media_box
            from codex_pdf.render.separations import sample_color

            if page_w is None or page_h is None:
                mb = get_page_media_box(raw, page)
                page_w = mb[2] - mb[0]
                page_h = mb[3] - mb[1]
            res = sample_color(raw, page, x=x, y=y, page_w=page_w, page_h=page_h, dpi=dpi)
            return ColorSample(
                x=float(res["x"]),
                y=float(res["y"]),
                dpi=int(res["dpi"]),
                rgb=tuple(res["rgb"]),  # type: ignore[arg-type]
                hex=str(res["hex"]),
            )
        fields: dict[str, Any] = {"page": page, "x": x, "y": y, "dpi": dpi}
        if page_w is not None:
            fields["page_w"] = page_w
        if page_h is not None:
            fields["page_h"] = page_h
        body, boundary = _build_multipart(raw, fields=fields)
        _status, response_body, _headers = self._post(
            "/v1/sample/color",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="application/json",
        )
        payload = json.loads(response_body)
        return ColorSample(
            x=float(payload["x"]),
            y=float(payload["y"]),
            dpi=int(payload["dpi"]),
            rgb=tuple(payload["rgb"]),  # type: ignore[arg-type]
            hex=str(payload["hex"]),
        )

    def sample_density(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
        x: float,
        y: float,
        page_w: float | None = None,
        page_h: float | None = None,
        dpi: int = 300,
        tac_limit: float = 300,
    ) -> DensitometerSample:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render._common import get_page_media_box
            from codex_pdf.render.separations import sample_density

            if page_w is None or page_h is None:
                mb = get_page_media_box(raw, page)
                page_w = mb[2] - mb[0]
                page_h = mb[3] - mb[1]
            res = sample_density(
                raw, page, x=x, y=y, page_w=page_w, page_h=page_h, dpi=dpi, tac_limit=tac_limit
            )
            return DensitometerSample(
                x=float(res["x"]),
                y=float(res["y"]),
                dpi=int(res["dpi"]),
                channels=list(res["channels"]),  # type: ignore[arg-type]
                tac=float(res["tac"]),
                tac_limit=float(res["tac_limit"]),
                limit_exceeded=bool(res["limit_exceeded"]),
            )
        fields: dict[str, Any] = {
            "page": page,
            "x": x,
            "y": y,
            "dpi": dpi,
            "tac_limit": tac_limit,
        }
        if page_w is not None:
            fields["page_w"] = page_w
        if page_h is not None:
            fields["page_h"] = page_h
        body, boundary = _build_multipart(raw, fields=fields)
        _status, response_body, _headers = self._post(
            "/v1/sample/density",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="application/json",
        )
        payload = json.loads(response_body)
        return DensitometerSample(
            x=float(payload["x"]),
            y=float(payload["y"]),
            dpi=int(payload["dpi"]),
            channels=list(payload["channels"]),
            tac=float(payload["tac"]),
            tac_limit=float(payload["tac_limit"]),
            limit_exceeded=bool(payload["limit_exceeded"]),
        )

    def eval_type4(
        self,
        program: str,
        inputs: list[float] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a PDF Type-4 PostScript function via codex.

        Returns ``{"result": [...] | None, "fast_path": bool}``. When
        the program is a trivially-constant tint transform we return
        without contacting the server (matches Python in-process
        fast-path so lint-pdf parity stays byte-equal).
        """
        inputs = list(inputs or [])
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.eval.ps_type4 import _fast_path_constants, evaluate

            fast = _fast_path_constants(program)
            result = evaluate(program, inputs=inputs)
            return {"result": result, "fast_path": fast is not None}

        body = json.dumps({"program": program, "inputs": inputs}).encode("utf-8")
        _status, response_body, _headers = self._post(
            "/v1/walk/type4",
            body=body,
            content_type="application/json",
            accept="application/json",
        )
        return json.loads(response_body)

    def walk_content_stream(
        self,
        pdf: bytes | BinaryIO | str | os.PathLike[str],
        *,
        page: int = 1,
    ) -> dict[str, Any]:
        raw = self._coerce_bytes(pdf)
        self._require_http_or_local()
        if not self.is_http:
            from codex_pdf.render.content_stream import walk_content_stream

            return walk_content_stream(raw, page_num=page)
        body, boundary = _build_multipart(raw, fields={"page": page})
        _status, response_body, _headers = self._post(
            "/v1/walk/content-stream",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            accept="application/json",
        )
        return json.loads(response_body)
