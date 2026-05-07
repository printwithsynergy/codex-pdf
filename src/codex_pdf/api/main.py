"""Codex HTTP API.

FastAPI service exposing the codex render and analysis surface so
lint-pdf and loupe-pdf consume PDF bytes through one canonical engine.

Endpoints:

- ``POST /v1/extract`` — multipart PDF or JSON ``{"url": ...}`` →
  CodexDocument JSON.
- ``POST /v1/render/page`` — params ``page``, ``dpi``, ``ocg_on[]``,
  ``ocg_off[]``, ``simulate_overprint`` → ``image/png``.
- ``POST /v1/render/separations`` — multipart ``pdf`` + ``page``,
  ``dpi`` → ``application/json`` (list of ``{name,type,png_b64}``).
- ``POST /v1/render/heatmap`` — params ``page``, ``dpi``,
  ``tac_limit`` → ``image/png`` (with ``X-Codex-Tac-Runs`` JSON
  header carrying per-run mean TAC).
- ``POST /v1/render/layer`` — params ``page``, ``layer_index``,
  ``all_layer_indices``, ``dpi`` → RGBA ``image/png``.
- ``POST /v1/sample/color`` — JSON body → ``ColorSample`` JSON.
- ``POST /v1/sample/density`` — JSON body → ``DensitometerSample``
  JSON.
- ``POST /v1/walk/content-stream`` — page → analysis signals JSON.
- ``GET /v1/healthz`` — liveness.
- ``GET /v1/version`` — codex package version.
- ``GET /v1/contract`` — contract manifest.
- ``GET /v1/schema/{name}`` — JSON schemas served from
  ``schemas/v1/<name>.schema.json``.
- ``GET /metrics`` — Prometheus metrics (when prometheus-client is
  installed; otherwise a 503 stub).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from codex_pdf.api.auth import authenticate
from codex_pdf.api.cache import cache_key, make_cache
from codex_pdf.api.url_ingest import fetch_pdf_from_url
from codex_pdf.extract import extract_from_path
from codex_pdf.render._common import OCGError, get_page_count, get_page_media_box
from codex_pdf.render.content_stream import walk_content_stream
from codex_pdf.render.layer import render_layer
from codex_pdf.render.page import render_page
from codex_pdf.render.separations import (
    render_heatmap,
    render_separations,
    sample_color,
    sample_density,
)
from codex_pdf.schema import codex_document_schema, load_published_schema
from codex_pdf.version import VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional prometheus metrics.
# ---------------------------------------------------------------------------

try:
    from prometheus_client import (  # type: ignore
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    REQUESTS = Counter(
        "codex_api_requests_total",
        "Codex API request count",
        ["endpoint", "status"],
    )
    LATENCY = Histogram(
        "codex_api_request_seconds",
        "Codex API request latency",
        ["endpoint"],
    )
    _HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover
    REQUESTS = None
    LATENCY = None
    CONTENT_TYPE_LATEST = "text/plain"  # type: ignore
    _HAS_PROMETHEUS = False


def _record(endpoint: str, status_code: int, duration: float) -> None:
    if not _HAS_PROMETHEUS or REQUESTS is None or LATENCY is None:
        return
    REQUESTS.labels(endpoint=endpoint, status=str(status_code)).inc()
    LATENCY.labels(endpoint=endpoint).observe(duration)


# ---------------------------------------------------------------------------
# Cache + schema directory.
# ---------------------------------------------------------------------------

_cache = make_cache()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _schemas_dir() -> Path:
    return _repo_root() / "schemas" / "v1"


# ---------------------------------------------------------------------------
# App.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="codex-pdf",
    version=VERSION,
    description=(
        "Authoritative PDF facts + render service for Think Neverland tools. "
        "lint-pdf and loupe-pdf consume this surface; they no longer parse "
        "PDF bytes themselves (exports remain in lint-pdf as one-off assets)."
    ),
)


# ---------------------------------------------------------------------------
# Request / response models.
# ---------------------------------------------------------------------------


class ExtractByUrl(BaseModel):
    url: str


class SampleColorRequest(BaseModel):
    page: int = Field(ge=1)
    x: float
    y: float
    page_w: float
    page_h: float
    dpi: int = Field(default=300, ge=36, le=900)


class SampleDensityRequest(SampleColorRequest):
    tac_limit: float = 300


class WalkContentStreamRequest(BaseModel):
    page: int = Field(default=1, ge=1)


class HealthResponse(BaseModel):
    status: str
    version: str
    ghostscript: bool


class VersionResponse(BaseModel):
    version: str


class ContractResponse(BaseModel):
    contract_name: str
    schema_version: str
    package_version: str
    schema_id: str
    endpoints: list[str]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


async def _read_pdf_bytes(file: UploadFile) -> bytes:
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing 'pdf' multipart field",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty pdf upload",
        )
    return raw


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid integer list: {raw!r}",
        ) from exc


# ---------------------------------------------------------------------------
# Health / version / contract / schema.
#
# `/healthz` is intentionally unauth'd so Railway / Cloudflare /
# uptime probes work without rotating credentials. `/v1/healthz` is
# the canonical path going forward; the un-versioned alias keeps
# legacy demo deploys (loupe-pdf-marketing, lint-pdf-marketing)
# working without redeploys.
# ---------------------------------------------------------------------------


@app.get("/healthz", response_model=HealthResponse, include_in_schema=False)
async def healthz_root() -> HealthResponse:
    return await healthz()


@app.get("/v1/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    from codex_pdf.render._common import has_ghostscript

    return HealthResponse(status="ok", version=VERSION, ghostscript=has_ghostscript())


@app.get("/v1/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(version=VERSION)


@app.get("/v1/contract", response_model=ContractResponse)
async def contract() -> ContractResponse:
    return ContractResponse(
        contract_name="codex-document",
        schema_version="1.0.0",
        package_version=VERSION,
        schema_id="https://schemas.thinkneverland.com/codex-pdf/v1/codex-document.schema.json",
        endpoints=[
            "POST /v1/extract",
            "POST /v1/render/page",
            "POST /v1/render/separations",
            "POST /v1/render/heatmap",
            "POST /v1/render/layer",
            "POST /v1/sample/color",
            "POST /v1/sample/density",
            "POST /v1/walk/content-stream",
            "GET /v1/healthz",
            "GET /v1/version",
            "GET /v1/contract",
            "GET /v1/schema/{name}",
            "GET /metrics",
        ],
    )


@app.get("/v1/schema/{name}")
async def schema_by_name(name: str) -> JSONResponse:
    safe = name.strip().lower().rstrip(".json")
    if not safe.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid schema name: {name!r}",
        )
    if safe == "codex-document":
        try:
            return JSONResponse(load_published_schema(_repo_root()))
        except FileNotFoundError:
            return JSONResponse(codex_document_schema())
    candidate = _schemas_dir() / f"{safe}.schema.json"
    if not candidate.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"schema not found: {name}",
        )
    return JSONResponse(json.loads(candidate.read_text(encoding="utf-8")))


@app.get("/metrics")
async def metrics() -> Response:
    if not _HAS_PROMETHEUS:
        return PlainTextResponse(
            "prometheus_client not installed", status_code=503, media_type="text/plain"
        )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Extract.
#
# Both un-versioned `/extract` (preserving the marketing demo flow)
# and `/v1/extract` (the canonical path) accept:
#
#   - multipart/form-data with a `pdf` field, OR
#   - JSON `{"url": "https://..."}` (also `s3_url` / `presigned_url`)
#     when ALLOW_EXTERNAL_FETCH=true.
#
# The shared implementation is :func:`_extract_impl`; both routes are
# thin wrappers so the cache key + observability counters stay
# consistent.
# ---------------------------------------------------------------------------


def _extract_url_from_json(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("url", "s3_url", "presigned_url"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _read_extract_pdf(request: Request, pdf: UploadFile | None) -> bytes:
    """Pull PDF bytes out of multipart or JSON URL body.

    Multipart wins when both are sent — the marketing demo always
    multipart-uploads. JSON URL bodies are gated by
    ``ALLOW_EXTERNAL_FETCH`` (handled inside ``fetch_pdf_from_url``).
    """
    if pdf is not None:
        return await _read_pdf_bytes(pdf)

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid JSON body: {exc}",
            ) from exc
        url = _extract_url_from_json(body)
        if not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "JSON body must include 'url' (or 's3_url' / 'presigned_url')"
                ),
            )
        return fetch_pdf_from_url(url)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "POST /extract requires either multipart 'pdf' field or JSON body "
            "with a 'url' field"
        ),
    )


def _run_extract(raw: bytes) -> dict[str, Any]:
    key = cache_key(raw, {}, kind="extract")
    cached = _cache.get(key)
    if cached is not None:
        return json.loads(cached)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        doc = extract_from_path(Path(tmp_path))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    payload = doc.model_dump(mode="json")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


async def _extract_impl(
    request: Request, pdf: UploadFile | None, *, endpoint_label: str
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw = await _read_extract_pdf(request, pdf)
        payload = _run_extract(raw)
        _record(endpoint_label, 200, time.perf_counter() - started)
        return JSONResponse(payload)
    except HTTPException as exc:
        _record(endpoint_label, exc.status_code, time.perf_counter() - started)
        raise
    except Exception as exc:
        logger.exception("extract failed")
        _record(endpoint_label, 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"extract failed: {exc}",
        ) from exc


@app.post("/extract", include_in_schema=False, dependencies=[Depends(authenticate)])
async def extract_root_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
) -> JSONResponse:
    return await _extract_impl(request, pdf, endpoint_label="extract")


@app.post("/v1/extract", dependencies=[Depends(authenticate)])
async def extract_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
) -> JSONResponse:
    """Extract a CodexDocument from an uploaded PDF or remote URL."""
    return await _extract_impl(request, pdf, endpoint_label="extract")


# ---------------------------------------------------------------------------
# Render.
# ---------------------------------------------------------------------------


@app.post("/v1/render/page", dependencies=[Depends(authenticate)])
async def render_page_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    dpi: int = Form(default=300),
    ocg_on: str | None = Form(default=None),
    ocg_off: str | None = Form(default=None),
    simulate_overprint: bool = Form(default=True),
) -> Response:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_bytes(pdf)
        on_list = _parse_int_list(ocg_on)
        off_list = _parse_int_list(ocg_off)
        args = {
            "page": page,
            "dpi": dpi,
            "ocg_on": on_list,
            "ocg_off": off_list,
            "simulate_overprint": simulate_overprint,
        }
        key = cache_key(raw, args, kind="page")
        cached = _cache.get(key)
        if cached is not None:
            _record("render_page", 200, time.perf_counter() - started)
            return Response(cached, media_type="image/png")

        png = render_page(
            raw,
            page,
            dpi=dpi,
            ocg_on=on_list,
            ocg_off=off_list,
            simulate_overprint=simulate_overprint,
        )
        _cache.set(key, png)
        _record("render_page", 200, time.perf_counter() - started)
        return Response(png, media_type="image/png")
    except OCGError as exc:
        _record("render_page", 422, time.perf_counter() - started)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("render_page failed")
        _record("render_page", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"render_page failed: {exc}",
        ) from exc


@app.post("/v1/render/separations", dependencies=[Depends(authenticate)])
async def render_separations_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    dpi: int = Form(default=150),
) -> JSONResponse:
    """Render every channel for one page in a single tiffsep call.

    Returns a manifest body
    ``{"page_num", "dpi", "channels": [{"name", "type", "png_b64"}]}``
    so callers don't have to multiplex multipart/related.
    """
    started = time.perf_counter()
    try:
        raw = await _read_pdf_bytes(pdf)
        args = {"page": page, "dpi": dpi}
        key = cache_key(raw, args, kind="separations")
        cached = _cache.get(key)
        if cached is not None:
            _record("render_separations", 200, time.perf_counter() - started)
            return JSONResponse(json.loads(cached))

        result = render_separations(raw, page, dpi=dpi)
        encoded_channels = [
            {
                "name": ch["name"],
                "type": ch["type"],
                "png_b64": base64.b64encode(ch["png"]).decode("ascii"),
            }
            for ch in result["channels"]
        ]
        body = {
            "page_num": result["page_num"],
            "dpi": result["dpi"],
            "channels": encoded_channels,
        }
        _cache.set(key, json.dumps(body, sort_keys=True).encode("utf-8"))
        _record("render_separations", 200, time.perf_counter() - started)
        return JSONResponse(body)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("render_separations failed")
        _record("render_separations", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"render_separations failed: {exc}",
        ) from exc


@app.post("/v1/render/heatmap", dependencies=[Depends(authenticate)])
async def render_heatmap_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    dpi: int = Form(default=150),
    tac_limit: float = Form(default=300),
) -> Response:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_bytes(pdf)
        args = {"page": page, "dpi": dpi, "tac_limit": tac_limit}
        key = cache_key(raw, args, kind="heatmap")
        cached = _cache.get(key)
        if cached is not None:
            _record("render_heatmap", 200, time.perf_counter() - started)
            payload = json.loads(cached)
            png = base64.b64decode(payload["png_b64"])
            headers = {"X-Codex-Tac-Runs": json.dumps(payload["runs"])}
            return Response(png, media_type="image/png", headers=headers)

        result = render_heatmap(raw, page, dpi=dpi, tac_limit=tac_limit)
        png = result["png"]
        runs = result["runs"]
        body = {
            "png_b64": base64.b64encode(png).decode("ascii"),
            "runs": runs,
        }
        _cache.set(key, json.dumps(body).encode("utf-8"))
        _record("render_heatmap", 200, time.perf_counter() - started)
        headers = {"X-Codex-Tac-Runs": json.dumps(runs)}
        return Response(png, media_type="image/png", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("render_heatmap failed")
        _record("render_heatmap", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"render_heatmap failed: {exc}",
        ) from exc


@app.post("/v1/render/layer", dependencies=[Depends(authenticate)])
async def render_layer_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    layer_index: int = Form(...),
    all_layer_indices: str = Form(...),
    dpi: int = Form(default=150),
) -> Response:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_bytes(pdf)
        all_idx = _parse_int_list(all_layer_indices)
        args = {
            "page": page,
            "layer_index": layer_index,
            "all_layer_indices": all_idx,
            "dpi": dpi,
        }
        key = cache_key(raw, args, kind="layer")
        cached = _cache.get(key)
        if cached is not None:
            _record("render_layer", 200, time.perf_counter() - started)
            return Response(cached, media_type="image/png")

        png = render_layer(
            raw,
            page,
            layer_index=layer_index,
            all_layer_indices=all_idx,
            dpi=dpi,
        )
        _cache.set(key, png)
        _record("render_layer", 200, time.perf_counter() - started)
        return Response(png, media_type="image/png")
    except OCGError as exc:
        _record("render_layer", 422, time.perf_counter() - started)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("render_layer failed")
        _record("render_layer", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"render_layer failed: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Sample / walk.
# ---------------------------------------------------------------------------


async def _read_pdf_field(pdf: UploadFile | None) -> bytes:
    if pdf is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="multipart 'pdf' field required",
        )
    return await _read_pdf_bytes(pdf)


@app.post("/v1/sample/color", dependencies=[Depends(authenticate)])
async def sample_color_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    x: float = Form(...),
    y: float = Form(...),
    page_w: float | None = Form(default=None),
    page_h: float | None = Form(default=None),
    dpi: int = Form(default=300),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_field(pdf)
        if page_w is None or page_h is None:
            mb = get_page_media_box(raw, page)
            page_w = mb[2] - mb[0]
            page_h = mb[3] - mb[1]
        args = {"page": page, "x": x, "y": y, "page_w": page_w, "page_h": page_h, "dpi": dpi}
        key = cache_key(raw, args, kind="sample-color")
        cached = _cache.get(key)
        if cached is not None:
            _record("sample_color", 200, time.perf_counter() - started)
            return JSONResponse(json.loads(cached))
        result = sample_color(
            raw, page, x=x, y=y, page_w=page_w, page_h=page_h, dpi=dpi
        )
        _cache.set(key, json.dumps(result).encode("utf-8"))
        _record("sample_color", 200, time.perf_counter() - started)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sample_color failed")
        _record("sample_color", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"sample_color failed: {exc}",
        ) from exc


@app.post("/v1/sample/density", dependencies=[Depends(authenticate)])
async def sample_density_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
    x: float = Form(...),
    y: float = Form(...),
    page_w: float | None = Form(default=None),
    page_h: float | None = Form(default=None),
    dpi: int = Form(default=300),
    tac_limit: float = Form(default=300),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_field(pdf)
        if page_w is None or page_h is None:
            mb = get_page_media_box(raw, page)
            page_w = mb[2] - mb[0]
            page_h = mb[3] - mb[1]
        args = {
            "page": page,
            "x": x,
            "y": y,
            "page_w": page_w,
            "page_h": page_h,
            "dpi": dpi,
            "tac_limit": tac_limit,
        }
        key = cache_key(raw, args, kind="sample-density")
        cached = _cache.get(key)
        if cached is not None:
            _record("sample_density", 200, time.perf_counter() - started)
            return JSONResponse(json.loads(cached))
        result = sample_density(
            raw, page, x=x, y=y, page_w=page_w, page_h=page_h, dpi=dpi, tac_limit=tac_limit
        )
        _cache.set(key, json.dumps(result).encode("utf-8"))
        _record("sample_density", 200, time.perf_counter() - started)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sample_density failed")
        _record("sample_density", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"sample_density failed: {exc}",
        ) from exc


@app.post("/v1/walk/content-stream", dependencies=[Depends(authenticate)])
async def walk_content_stream_endpoint(
    pdf: UploadFile = File(...),
    page: int = Form(default=1),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw = await _read_pdf_field(pdf)
        args = {"page": page}
        key = cache_key(raw, args, kind="walk-content-stream")
        cached = _cache.get(key)
        if cached is not None:
            _record("walk_content_stream", 200, time.perf_counter() - started)
            return JSONResponse(json.loads(cached))
        result = walk_content_stream(raw, page_num=page)
        _cache.set(key, json.dumps(result).encode("utf-8"))
        _record("walk_content_stream", 200, time.perf_counter() - started)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("walk_content_stream failed")
        _record("walk_content_stream", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"walk_content_stream failed: {exc}",
        ) from exc
