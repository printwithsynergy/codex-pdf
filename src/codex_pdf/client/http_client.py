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
import hashlib
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


@dataclass(frozen=True)
class RouteTarget:
    base_url: str
    plant: str | None = None
    role: str | None = None


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
        base_urls: list[str] | None = None,
        plant: str | None = None,
        route_mode: str | None = None,
        affinity_key: str | None = None,
        required_section_versions: dict[str, str] | None = None,
        bearer_token: str | None = None,
        api_key: str | None = None,
        internal_token: str | None = None,
        timeout_ms: int | None = None,
        max_retries: int = 3,
        local_fallback: bool | None = None,
    ) -> None:
        self.targets = self._load_targets(base_url=base_url, base_urls=base_urls)
        self.base_url = self.targets[0].base_url if self.targets else None
        self.plant = (plant or os.environ.get("CODEX_PLANT") or "").strip() or None
        detected_route_mode = route_mode or os.environ.get("CODEX_ROUTE_MODE")
        if detected_route_mode:
            self.route_mode = detected_route_mode.strip().lower()
        else:
            self.route_mode = "hybrid" if len(self.targets) > 1 else "single"
        self.affinity_key = (
            affinity_key
            or os.environ.get("CODEX_AFFINITY_KEY")
            or os.environ.get("CODEX_PLANT_AFFINITY_KEY")
        )
        self.required_section_versions = required_section_versions or self._load_required_sections()
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
        self._contract_cache: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------
    # Mode selection.
    # -----------------------------------------------------------------

    @property
    def is_http(self) -> bool:
        return bool(self.targets)

    def _require_http_or_local(self) -> str | None:
        if self.is_http:
            return self.targets[0].base_url
        if self.local_fallback:
            return None
        raise CodexClientError(
            "CODEX_API_BASE not configured and CODEX_LOCAL_FALLBACK disabled",
            status=-1,
        )

    # -----------------------------------------------------------------
    # Low-level HTTP helpers (urllib so we have no third-party dep).
    # -----------------------------------------------------------------

    def _headers(
        self,
        *,
        target: RouteTarget | None = None,
        request_id: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.api_key:
            headers["X-Codex-Key"] = self.api_key
        if self.internal_token:
            headers["X-Codex-Internal"] = self.internal_token
        headers["X-Codex-Route-Mode"] = self.route_mode
        if request_id:
            headers["X-Codex-Request-Id"] = request_id
        if self.affinity_key:
            headers["X-Codex-Affinity-Key"] = self.affinity_key
        effective_plant = self.plant or (target.plant if target else None)
        if effective_plant:
            headers["X-Codex-Plant"] = effective_plant
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
        request_id = secrets.token_hex(8)
        last_exc: Exception | None = None
        for target in self._ordered_targets():
            try:
                self._ensure_contract_compatible(target, request_id=request_id)
            except CodexClientError as exc:
                last_exc = exc
                logger.warning("codex target skipped due to contract mismatch: %s", target.base_url)
                continue
            url = target.base_url.rstrip("/") + path
            headers = self._headers(
                target=target,
                request_id=request_id,
                extra={"Content-Type": content_type, "Accept": accept},
            )
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
                        logger.warning(
                            "codex %s -> %d on %s, retry %d in %.1fs",
                            path,
                            exc.code,
                            target.base_url,
                            attempt,
                            backoff,
                        )
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
                    logger.warning(
                        "codex %s transport failure on %s, retry %d in %.1fs",
                        path,
                        target.base_url,
                        attempt,
                        backoff,
                    )
                    time.sleep(backoff)
            logger.warning("codex target failed after retries: %s", target.base_url)
        raise CodexClientError(
            f"codex {path} failed across {max(1, len(self.targets))} targets: {last_exc}",
            status=-1,
        )

    def _get(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        request_id = secrets.token_hex(8)
        last_exc: Exception | None = None
        for target in self._ordered_targets():
            try:
                self._ensure_contract_compatible(target, request_id=request_id)
            except CodexClientError as exc:
                last_exc = exc
                continue
            url = target.base_url.rstrip("/") + path
            req = urlrequest.Request(
                url,
                headers=self._headers(target=target, request_id=request_id),
                method="GET",
            )
            try:
                with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                    return resp.getcode(), resp.read(), dict(resp.headers.items())
            except urlerror.HTTPError as exc:
                if exc.code in {408, 429} or 500 <= exc.code < 600:
                    last_exc = exc
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                raise CodexClientError(
                    f"codex {path} -> {exc.code}: {detail}",
                    status=exc.code,
                    body=detail,
                ) from exc
            except Exception as exc:
                last_exc = exc
                continue
        raise CodexClientError(
            f"codex {path} failed across {max(1, len(self.targets))} targets: {last_exc}",
            status=-1,
        )

    def _load_targets(
        self,
        *,
        base_url: str | None,
        base_urls: list[str] | None,
    ) -> list[RouteTarget]:
        targets: list[RouteTarget] = []
        if base_url:
            targets.append(RouteTarget(base_url=base_url.strip()))
            return targets
        if base_urls:
            targets.extend(
                RouteTarget(base_url=url.strip()) for url in base_urls if isinstance(url, str) and url.strip()
            )
            if targets:
                return targets

        pool_json_raw = (os.environ.get("CODEX_API_POOL_JSON") or "").strip()
        if pool_json_raw:
            try:
                pool = json.loads(pool_json_raw)
                if isinstance(pool, list):
                    for item in pool:
                        if not isinstance(item, dict):
                            continue
                        url = str(item.get("base_url") or item.get("url") or "").strip()
                        if not url:
                            continue
                        plant = str(item.get("plant") or "").strip() or None
                        role = str(item.get("role") or "").strip() or None
                        targets.append(RouteTarget(base_url=url, plant=plant, role=role))
                elif isinstance(pool, dict):
                    for plant, url in pool.items():
                        if not isinstance(url, str) or not url.strip():
                            continue
                        targets.append(RouteTarget(base_url=url.strip(), plant=str(plant)))
            except Exception:
                logger.warning("failed to parse CODEX_API_POOL_JSON; falling back to CODEX_API_BASE")
        if targets:
            return targets

        bases_raw = (os.environ.get("CODEX_API_BASES") or "").strip()
        if bases_raw:
            for token in bases_raw.split(","):
                cleaned = token.strip()
                if not cleaned:
                    continue
                if "=" in cleaned:
                    plant, url = cleaned.split("=", 1)
                    targets.append(RouteTarget(base_url=url.strip(), plant=plant.strip() or None))
                else:
                    targets.append(RouteTarget(base_url=cleaned))
            if targets:
                return targets

        env_base = (os.environ.get("CODEX_API_BASE") or "").strip()
        if env_base:
            targets.append(RouteTarget(base_url=env_base))
        return targets

    def _ordered_targets(self) -> list[RouteTarget]:
        if not self.targets:
            return []
        if self.route_mode == "single":
            return [self.targets[0]]

        same_plant: list[RouteTarget] = []
        others: list[RouteTarget] = []
        for target in self.targets:
            if self.plant and target.plant and target.plant == self.plant:
                same_plant.append(target)
            else:
                others.append(target)
        if not same_plant:
            same_plant = []
            others = list(self.targets)

        ordered_primary = self._rotate_by_affinity(same_plant or [self.targets[0]])
        ordered_secondary = self._rotate_by_affinity(others)
        if self.route_mode == "plant":
            return ordered_primary
        if self.route_mode == "failover":
            return ordered_primary + ordered_secondary
        # hybrid (default): plant-affine first, then failover pool.
        return ordered_primary + [t for t in ordered_secondary if t not in ordered_primary]

    def _rotate_by_affinity(self, targets: list[RouteTarget]) -> list[RouteTarget]:
        if len(targets) <= 1 or not self.affinity_key:
            return list(targets)
        digest = hashlib.sha256(self.affinity_key.encode("utf-8", "ignore")).digest()
        offset = int.from_bytes(digest[:4], "big") % len(targets)
        return targets[offset:] + targets[:offset]

    def _load_required_sections(self) -> dict[str, str]:
        raw = (os.environ.get("CODEX_REQUIRED_SECTION_VERSIONS") or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            logger.warning("failed to parse CODEX_REQUIRED_SECTION_VERSIONS JSON")
            return {}
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in parsed.items():
            if isinstance(key, str) and isinstance(value, str) and key and value:
                out[key] = value
        return out

    def _ensure_contract_compatible(self, target: RouteTarget, *, request_id: str) -> None:
        if not self.required_section_versions:
            return
        cached = self._contract_cache.get(target.base_url)
        if cached is None:
            url = target.base_url.rstrip("/") + "/v1/contract"
            req = urlrequest.Request(
                url,
                headers=self._headers(target=target, request_id=request_id),
                method="GET",
            )
            try:
                with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                    payload = json.loads(resp.read() or b"{}")
            except Exception as exc:
                raise CodexClientError(
                    f"failed to read contract from {target.base_url}: {exc}",
                    status=-1,
                ) from exc
            if not isinstance(payload, dict):
                raise CodexClientError(
                    f"invalid contract payload from {target.base_url}",
                    status=-1,
                )
            cached = payload
            self._contract_cache[target.base_url] = cached

        section_versions = cached.get("section_schema_versions") or {}
        if not isinstance(section_versions, dict):
            section_versions = {}
        for section, required in self.required_section_versions.items():
            got = section_versions.get(section)
            if got is None:
                raise CodexClientError(
                    f"{target.base_url} missing required section '{section}' in contract",
                    status=-1,
                )
            if str(got) != str(required):
                raise CodexClientError(
                    f"{target.base_url} incompatible section '{section}': required {required}, got {got}",
                    status=-1,
                )

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
