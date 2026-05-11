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

import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import structlog
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
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from codex_pdf.api.auth import authenticate
from codex_pdf.api.blob_store import make_blob_store
from codex_pdf.api.cache import cache_key, make_cache
from codex_pdf.api.retention import (
    make_retention_store,
    normalise_tenant,
    parse_retention_consent,
)
from codex_pdf.api.warmup import warmup_worker
from codex_pdf.api.url_ingest import fetch_pdf_from_url
from codex_pdf.color import (
    COLOR_SCHEMA_VERSION,
    CodexSpotIntent,
    SpotInkOverride,
    delta_e_2000,
    load_inkbook,
    load_pantone_reference,
    match_nearest_pantone,
    resolve_spot_swatch_color,
)
from codex_pdf.color.color_math import lab_d50_to_srgb, srgb_decode
from codex_pdf.extract import (
    assemble_codex_document,
    extract_document,
    extract_document_fast,
    extract_document_pymupdf_only,
    extract_from_path,
    extract_probe_min,
    extract_probe_std,
)
from codex_pdf.extract.color import extract_color_world_pikepdf
from codex_pdf.extract.document import _EXTRACT_POOL, _run_fitz_pipeline
from codex_pdf.extract.forms import extract_forms_pikepdf
from codex_pdf.extract.ocg import extract_ocgs_pikepdf
from codex_pdf.extract.signals import extract_analysis_signals_pikepdf
from codex_pdf.geom import (
    GEOM_SCHEMA_VERSION,
    Box as GeomBox,
    CellPlacement,
    MarksZone,
    Path as GeomPath,
    TileGrid,
    polygon_difference,
    polygon_intersect,
    polygon_offset,
    polygon_union,
    tile_grid,
)
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
_blob_store = make_blob_store()
_retention_store = make_retention_store()


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

_INSTANCE_ID: str = os.environ.get("CODEX_INSTANCE_ID") or socket.gethostname()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("X-Codex-Request-Id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Codex-Request-Id"] = request_id
        return response


app.add_middleware(RequestIdMiddleware)


@app.on_event("startup")
async def _warmup_on_startup() -> None:
    """Run :func:`warmup_worker` once per gunicorn worker on boot.

    Adds ~500 ms-1.5 s to worker boot but eliminates the cold-start
    penalty on the first real request. Safe under gunicorn pre-fork:
    the startup hook fires per worker process after fork, so each
    worker's pool is independently primed.
    """
    try:
        timings = warmup_worker()
        app.state.warmed_up = True
        app.state.warmup_timings = timings
    except Exception:
        logger.exception("warmup hook failed")
        app.state.warmed_up = False


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


class Type4Request(BaseModel):
    """JSON body for ``POST /v1/walk/type4``.

    Mirrors the in-process API of :func:`codex_pdf.eval.ps_type4.evaluate`.
    """

    program: str = Field(..., min_length=1, max_length=16384)
    inputs: list[float] = Field(default_factory=list, max_length=64)


class Type4Response(BaseModel):
    """Response shape — ``result`` is None when codex couldn't verify."""

    result: list[float] | None
    fast_path: bool


class HealthResponse(BaseModel):
    status: str
    version: str
    ghostscript: bool
    cache_backend: str
    instance_id: str | None = None


class VersionResponse(BaseModel):
    version: str


class ContractResponse(BaseModel):
    contract_name: str
    schema_version: str
    package_version: str
    schema_id: str
    endpoints: list[str]
    section_schema_versions: dict[str, str] = Field(default_factory=dict)


# Color request/response models. Each field is optional; the resolver
# picks the strongest signal it has (host > codex > pantone > curated
# > hash). All numeric ranges are validated at the Pydantic boundary
# so the resolver itself never receives malformed Lab/CMYK/RGB.
LabValue = list[float]
CmykValue = list[float]
RgbValue = list[int]


class ColorOverride(BaseModel):
    rgb: RgbValue | None = None
    lab: LabValue | None = None
    cmyk: CmykValue | None = None
    pantone_name: str | None = None


class ColorResolveRequest(BaseModel):
    """Body for ``POST /v1/color/resolve``.

    ``name`` is the spot ink's canonical name (e.g. ``"PANTONE 485 C"``,
    ``"Cut"``, ``"Varnish"``). ``host_override`` and ``codex`` carry
    optional intent signals.
    """

    name: str = Field(..., min_length=1, max_length=256)
    host_override: ColorOverride | None = None
    codex: ColorOverride | None = None
    extra_pantone_overrides: dict[str, dict[str, object]] | None = None


class ColorResolveResponse(BaseModel):
    schema_version: str
    rgb: RgbValue
    source: str
    lab: LabValue | None = None
    cmyk: CmykValue | None = None
    pantone_name: str | None = None


class ColorMatchPantoneRequest(BaseModel):
    """Body for ``POST /v1/color/match-pantone``.

    Provide a Lab triple, a CMYK quad, or an RGB triple. The endpoint
    converts to Lab as needed before searching the catalogue. Library
    filter mirrors :func:`codex_pdf.color.iter_pantone_entries` —
    ``["*"]`` for the full 23k-entry catalogue, ``None`` (default) for
    Formula Guide Coated + Uncoated.
    """

    lab: LabValue | None = None
    cmyk: CmykValue | None = None
    rgb: RgbValue | None = None
    libraries: list[str] | None = None


class ColorMatchPantoneResponse(BaseModel):
    schema_version: str
    pantone_name: str
    library: str | None
    delta_e: float
    lab: LabValue
    cmyk: CmykValue | None = None
    rgb: RgbValue


class GeomBoxModel(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class GeomMarksZoneModel(BaseModel):
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0


class NeutralDensityRequest(BaseModel):
    """Body for ``POST /v1/color/neutral-density``.

    Provide exactly one of ``name`` (resolved via spot resolver),
    ``lab`` (CIE Lab D50 triple), or ``cmyk`` (0–100 quad). Lab and
    CMYK inputs bypass the spot resolver and compute ND directly.
    """

    name: str | None = Field(default=None, min_length=1, max_length=256)
    lab: LabValue | None = None
    cmyk: CmykValue | None = None


class NeutralDensityResponse(BaseModel):
    schema_version: str
    neutral_density: float
    source: str


class GeomTileRequest(BaseModel):
    sheet: GeomBoxModel
    cell_width: float = Field(..., gt=0)
    cell_height: float = Field(..., gt=0)
    gutter_x: float = Field(default=0.0, ge=0)
    gutter_y: float = Field(default=0.0, ge=0)
    marks_zone: GeomMarksZoneModel = GeomMarksZoneModel()
    origin: str = Field(default="bottom-left", pattern="^(bottom-left|top-left)$")
    # §16.2 extension fields
    cell_rotation: float = 0.0
    cell_rotation_pattern: list[list[float]] | None = None
    flip_per_row: bool = False
    flip_pattern: list[list[bool]] | None = None
    bleed_handling: str = Field(default="none", pattern="^(none|trim|extend)$")
    bleed: float = Field(default=0.0, ge=0)


class CellPlacementModel(BaseModel):
    box: list[float]
    rotation: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    row: int = 0
    col: int = 0


class GeomTileResponse(BaseModel):
    schema_version: str
    rows: int
    cols: int
    cells: list[list[float]]
    placements: list[CellPlacementModel] = Field(default_factory=list)
    used: list[float]
    waste: list[float]


class GeomOffsetRequest(BaseModel):
    """Body for ``POST /v1/geom/offset``.

    ``path`` is a list of polygon rings (each ring is a list of ``[x, y]``
    points). ``distance_pt`` is the offset distance in PDF user-space points;
    negative values shrink (choke), positive values grow (spread).
    """

    path: list[list[list[float]]]
    distance_pt: float
    join_type: str = Field(default="miter", pattern="^(miter|round|square)$")
    end_type: str = Field(
        default="polygon",
        pattern="^(polygon|joined_round|joined_square|butt|square|round)$",
    )
    miter_limit: float = Field(default=2.0, gt=0)


class GeomOffsetResponse(BaseModel):
    schema_version: str
    rings: list[list[list[float]]]


class GeomBooleanRequest(BaseModel):
    """Body for ``POST /v1/geom/{intersect,union,difference}``.

    ``subjects`` and ``clips`` are lists of paths; each path is a list
    of polygon rings; each ring is a list of ``[x, y]`` points. The
    server uses pyclipr (Clipper2) for non-rectangular paths and
    pure-Python rectangle math otherwise.
    """

    subjects: list[list[list[list[float]]]]
    clips: list[list[list[list[float]]]] | None = None


class GeomBooleanResponse(BaseModel):
    schema_version: str
    rings: list[list[list[float]]]


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


async def _resolve_pdf_bytes(
    pdf: UploadFile | None,
    pdf_sha256: str | None,
) -> tuple[bytes, str]:
    """Get PDF bytes from either a multipart upload or the blob store.

    Caches uploads by sha256 so subsequent calls in the same session
    can pass ``pdf_sha256`` instead of re-uploading the file. Returns
    ``(raw_bytes, sha256_hex)``. Raises ``412`` if the hash isn't in
    the blob store and no upload was provided.
    """
    if pdf is not None:
        raw = await pdf.read()
        if raw:
            sha = hashlib.sha256(raw).hexdigest()
            _blob_store.put(sha, raw)
            return raw, sha
        # Fall through to blob lookup if upload was empty/blank but
        # a hash was also provided (browser quirks with optional
        # multipart fields).
    if pdf_sha256:
        cleaned = pdf_sha256.strip()
        if cleaned:
            cached = _blob_store.get(cleaned)
            if cached is not None:
                return cached, cleaned
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=(
                    f"pdf_sha256 {cleaned[:16]}... not in cache (expired or never "
                    "uploaded). Re-upload the PDF as a multipart 'pdf' field."
                ),
            )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="must provide either 'pdf' multipart field or 'pdf_sha256' form field",
    )


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

    return HealthResponse(
        status="ok",
        version=VERSION,
        ghostscript=has_ghostscript(),
        cache_backend=getattr(_cache, "name", type(_cache).__name__.lower()),
        instance_id=_INSTANCE_ID,
    )


@app.get("/v1/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(version=VERSION)


@app.get("/v1/contract", response_model=ContractResponse)
async def contract() -> ContractResponse:
    return ContractResponse(
        contract_name="codex-document",
        schema_version="1.1.0",
        package_version=VERSION,
        schema_id="https://schemas.thinkneverland.com/codex-pdf/v1/codex-document.schema.json",
        endpoints=[
            "POST /v1/extract",
            "POST /v1/extract/stream",
            "POST /v1/probe",
            "POST /v1/render/page",
            "POST /v1/render/separations",
            "POST /v1/render/heatmap",
            "POST /v1/render/layer",
            "POST /v1/sample/color",
            "POST /v1/sample/density",
            "POST /v1/walk/content-stream",
            "POST /v1/walk/type4",
            "POST /v1/color/resolve",
            "POST /v1/color/match-pantone",
            "GET /v1/color/inkbook",
            "POST /v1/color/neutral-density",
            "POST /v1/geom/tile",
            "POST /v1/geom/intersect",
            "POST /v1/geom/union",
            "POST /v1/geom/difference",
            "POST /v1/geom/offset",
            "GET /v1/healthz",
            "GET /v1/version",
            "GET /v1/contract",
            "GET /v1/schema/{name}",
            "GET /metrics",
        ],
        section_schema_versions={
            "color": COLOR_SCHEMA_VERSION,
            "geom": GEOM_SCHEMA_VERSION,
        },
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

    doc = extract_document(raw)
    payload = doc.model_dump(mode="json")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


def _run_extract_phase1(raw: bytes) -> dict[str, Any]:
    """Phase 1 extract with sha-only cache key.

    Repeat traffic for the same PDF returns Phase 1 in <10 ms even if
    Phase 2 has never completed. The cache value is the JSON dump of
    the CodexDocument that ``extract_document_fast`` produces.
    """
    key = cache_key(raw, {}, kind="extract-phase-1")
    cached = _cache.get(key)
    if cached is not None:
        return json.loads(cached)

    doc = extract_document_fast(raw)
    payload = doc.model_dump(mode="json")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


def _run_extract_phase1_minimal(raw: bytes) -> dict[str, Any]:
    """PyMuPDF-only Phase 1 for the granular SSE path.

    Cached under ``kind="extract-phase-1-min"`` so the granular and
    standard streaming endpoints don't fight over the same key.
    """
    key = cache_key(raw, {}, kind="extract-phase-1-min")
    cached = _cache.get(key)
    if cached is not None:
        return json.loads(cached)
    doc = extract_document_pymupdf_only(raw)
    payload = doc.model_dump(mode="json")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


def _run_probe_min(raw: bytes) -> dict[str, Any]:
    key = cache_key(raw, {}, kind="probe-min")
    cached = _cache.get(key)
    if cached is not None:
        return json.loads(cached)
    payload = extract_probe_min(raw)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


def _run_probe_std(raw: bytes) -> dict[str, Any]:
    key = cache_key(raw, {}, kind="probe-std")
    cached = _cache.get(key)
    if cached is not None:
        return json.loads(cached)
    payload = extract_probe_std(raw)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _cache.set(key, body)
    return payload


def _pre_render_bg(raw: bytes) -> None:
    """Warm the render cache for page 1 at 72 DPI then 150 DPI.

    Runs in a thread pool. Two tiers so a thumbnail-only consumer
    (loupe sidebar, lint dashboard cards) gets a paintable image
    ~4x sooner than waiting for the full 150 DPI pre-render.
    """
    for dpi in (72, 150):
        try:
            pre_key = cache_key(raw, {"page": 1, "dpi": dpi}, kind="pre-render")
            if _cache.get(pre_key) is None:
                pre_png = render_page(
                    raw, 1, dpi=dpi, ocg_on=[], ocg_off=[], simulate_overprint=True
                )
                _cache.set(pre_key, pre_png)
        except Exception:
            pass


async def _extract_impl(
    request: Request,
    pdf: UploadFile | None,
    *,
    endpoint_label: str,
    retain_form_value: str | None = None,
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw = await _read_extract_pdf(request, pdf)
        sha = hashlib.sha256(raw).hexdigest()
        _blob_store.put(sha, raw)
        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(None, _run_extract, raw)
        # Surface the cache key so clients can switch to hash-based
        # render calls without re-uploading on every interaction.
        if isinstance(payload, dict):
            payload["pdf_sha256"] = sha
        # Warm the page-1 render cache in the background — don't block the response.
        asyncio.ensure_future(loop.run_in_executor(None, _pre_render_bg, raw))
        await _maybe_retain(request, raw, sha, payload, retain_form_value)
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


async def _maybe_retain(
    request: Request,
    raw: bytes,
    sha: str,
    payload: dict[str, Any],
    retain_form_value: str | None,
) -> None:
    """Audit-log the consent decision; persist to S3 if opted in.

    Audit log fires on every extract regardless of consent so the
    "off" path is observable. Persistence only runs when consent is
    affirmative AND retention is configured. A persist failure logs
    but does not break the extract response — the user already got
    their answer.
    """
    decision = parse_retention_consent(
        retain_form_value,
        request.headers.get("x-compile-retain-for-training"),
    )
    tenant = normalise_tenant(request.headers.get("x-codex-tenant"))
    request_id = request.headers.get("x-codex-request-id") or ""
    configured = _retention_store is not None
    logger.info(
        "extract_consent decision=%s source=%s mismatch=%s tenant=%s sha=%s "
        "request_id=%s retention_configured=%s",
        decision.consent,
        decision.source,
        decision.mismatch,
        tenant,
        sha[:16],
        request_id,
        configured,
    )
    if not (decision.consent and _retention_store is not None):
        return
    try:
        loop = asyncio.get_event_loop()
        keys = await loop.run_in_executor(
            None,
            lambda: _retention_store.put(
                pdf_bytes=raw,
                extract_payload=payload,
                request_id=request_id,
                tenant=tenant,
                sha256=sha,
                codex_version=VERSION,
                consent_source=decision.source,
            ),
        )
        logger.info(
            "extract_retained sha=%s tenant=%s keys=%s",
            sha[:16],
            tenant,
            list(keys.values()),
        )
    except Exception:
        logger.exception("retention persist failed sha=%s tenant=%s", sha[:16], tenant)


@app.post("/extract", include_in_schema=False, dependencies=[Depends(authenticate)])
async def extract_root_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
    retain_for_training: str | None = Form(default=None),
) -> JSONResponse:
    return await _extract_impl(
        request, pdf, endpoint_label="extract", retain_form_value=retain_for_training
    )


@app.post("/v1/extract", dependencies=[Depends(authenticate)])
async def extract_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
    retain_for_training: str | None = Form(default=None),
) -> JSONResponse:
    """Extract a CodexDocument from an uploaded PDF or remote URL."""
    return await _extract_impl(
        request, pdf, endpoint_label="extract", retain_form_value=retain_for_training
    )


class _RetentionDeleteRequest(BaseModel):
    sha256: str = Field(..., description="Lower-case hex SHA-256 of the PDF to erase.")


@app.post("/v1/retention/delete", dependencies=[Depends(authenticate)])
async def retention_delete_endpoint(payload: _RetentionDeleteRequest) -> JSONResponse:
    """Erase every retained object for ``sha256`` (DSAR endpoint).

    503 when retention is not configured (operator hasn't set
    ``CODEX_RETAIN_BUCKET``). 400 on a malformed sha256. ``deleted=0``
    when no objects matched — DSAR replay is idempotent, callers
    don't need to distinguish "never stored" from "already erased".
    """
    if _retention_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="retention not configured (CODEX_RETAIN_BUCKET unset)",
        )
    sha = payload.sha256.strip().lower()
    loop = asyncio.get_event_loop()
    try:
        deleted = await loop.run_in_executor(None, _retention_store.delete, sha)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    logger.info("retention_delete sha=%s deleted=%d", sha[:16], deleted)
    return JSONResponse({"sha256": sha, "deleted": deleted})


async def _read_probe_pdf(request: Request, pdf: UploadFile | None) -> bytes:
    """PDF source for /v1/probe.

    Multipart ``pdf`` field wins; otherwise accept a JSON body with
    ``{"pdf_sha256": "..."}`` and resolve via ``_blob_store``. URL
    ingestion is intentionally not supported here — probe targets sub-
    50 ms latency, which a network fetch would blow past.
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
        sha = None
        if isinstance(body, dict):
            candidate = body.get("pdf_sha256")
            if isinstance(candidate, str) and candidate.strip():
                sha = candidate.strip()
        if not sha:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON body must include 'pdf_sha256'",
            )
        cached = _blob_store.get(sha)
        if cached is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=(
                    f"pdf_sha256 {sha[:16]}... not in cache (expired or never "
                    "uploaded). Re-upload the PDF as a multipart 'pdf' field."
                ),
            )
        return cached

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "POST /v1/probe requires either multipart 'pdf' field or JSON body "
            "with a 'pdf_sha256' field"
        ),
    )


def _publish_speculate(sha: str, source: str) -> None:
    """Hand a sha to codex-speculator via Redis Stream ``codex:speculate``.

    Best-effort: any failure (no Redis client, stream unavailable,
    timeout) is logged and swallowed. Speculation is purely
    opportunistic — origin behaviour must never depend on it. A 60 s
    SET-NX dedupe collapses repeated probes / blob puts for the same
    sha into a single XADD.
    """
    try:
        client = getattr(_blob_store, "_client", None)
        if client is None:
            return
        if not client.set(f"codex:speculate:dedupe:{sha}", source, ex=60, nx=True):
            return
        client.xadd(
            "codex:speculate",
            {"sha": sha, "source": source},
            maxlen=10000,
            approximate=True,
        )
    except Exception:
        logger.debug("speculate publish failed for sha=%s source=%s", sha[:16], source, exc_info=True)


@app.post("/v1/probe", dependencies=[Depends(authenticate)])
async def probe_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
) -> StreamingResponse:
    """Two-event SSE probe — cheapest possible PDF facts.

    Event 1 (``probe_phase=1``): page count, first-page dimensions,
    encrypted flag. Target latency: <50 ms cold, <10 ms warm.

    Event 2 (``probe_phase=2``): full page-dimension list, info
    subset, PDF version. Target latency: <150 ms.

    Both events are content-addressed and cached; repeat probes for
    the same sha return from cache in single-digit milliseconds.
    """
    started = time.perf_counter()
    try:
        raw = await _read_probe_pdf(request, pdf)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {exc}") from exc

    sha = hashlib.sha256(raw).hexdigest()
    _blob_store.put(sha, raw)
    loop = asyncio.get_event_loop()

    async def _generate() -> AsyncIterator[str]:
        try:
            ev1 = await loop.run_in_executor(None, _run_probe_min, raw)
            ev1 = {**ev1, "pdf_sha256": sha, "probe_phase": 1}
            yield f"data: {json.dumps(ev1, separators=(',', ':'))}\n\n"
        except Exception:
            pass

        try:
            ev2 = await loop.run_in_executor(None, _run_probe_std, raw)
            ev2 = {**ev2, "pdf_sha256": sha, "probe_phase": 2}
            yield f"data: {json.dumps(ev2, separators=(',', ':'))}\n\n"
        except Exception:
            pass

        # Hand the sha off to codex-speculator so Phase 2 is warm by
        # the time the client follows up with /v1/extract.
        try:
            await loop.run_in_executor(None, _publish_speculate, sha, "probe")
        except Exception:
            pass
        _record("probe", 200, time.perf_counter() - started)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _model_dump_list(items: list) -> list:
    return [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in items]


async def _generate_granular_stream(raw: bytes, sha: str) -> AsyncIterator[str]:
    """Granular Phase 2 SSE: stream each pikepdf pass as its future settles.

    Order:
      1. ``event: phase1`` — PyMuPDF-only document.
      2. ``event: color_world`` / ``ocgs`` / ``form_xobjects`` /
         ``analysis`` — emitted in completion order, not submission
         order. Each carries ``pdf_sha256`` and the partial payload.
      3. ``event: phase2_complete`` — the merged full CodexDocument,
         identical to the cached ``/v1/extract`` response. Caches under
         ``kind="extract"`` so subsequent non-streaming requests hit.
    """
    loop = asyncio.get_event_loop()

    # Phase 1 (PyMuPDF-only) — granular path uses the lightest possible
    # baseline so color/ocgs/forms ride in as separate events later.
    p1: dict[str, Any] | None = None
    try:
        p1 = await loop.run_in_executor(None, _run_extract_phase1_minimal, raw)
        out = {**p1, "pdf_sha256": sha, "extract_phase": 1}
        yield f"event: phase1\ndata: {json.dumps(out, separators=(',', ':'))}\n\n"
    except Exception:
        logger.exception("granular phase1 failed")

    # Kick off the four pikepdf passes; await in completion order.
    f_color = asyncio.wrap_future(_EXTRACT_POOL.submit(extract_color_world_pikepdf, raw))
    f_ocgs = asyncio.wrap_future(_EXTRACT_POOL.submit(extract_ocgs_pikepdf, raw))
    f_forms = asyncio.wrap_future(_EXTRACT_POOL.submit(extract_forms_pikepdf, raw))
    f_signals = asyncio.wrap_future(_EXTRACT_POOL.submit(extract_analysis_signals_pikepdf, raw))
    pending: dict[asyncio.Future, str] = {
        f_color: "color_world",
        f_ocgs: "ocgs",
        f_forms: "form_xobjects",
        f_signals: "analysis",
    }

    output_intents: list = []
    color_spaces: list = []
    ocgs: list = []
    form_xobjects: list = []
    analysis: dict[str, Any] = {}

    while pending:
        done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            name = pending.pop(fut)
            try:
                value: Any = fut.result()
            except Exception:
                value = None
            if name == "color_world":
                if isinstance(value, tuple) and len(value) == 2:
                    output_intents, color_spaces = value
                payload = {
                    "output_intents": _model_dump_list(output_intents),
                    "color_spaces": _model_dump_list(color_spaces),
                }
            elif name == "ocgs":
                ocgs = value or []
                payload = {"ocgs": _model_dump_list(ocgs)}
            elif name == "form_xobjects":
                form_xobjects = value or []
                payload = {"form_xobjects": _model_dump_list(form_xobjects)}
            elif name == "analysis":
                analysis = value or {}
                payload = {"analysis": analysis}
            else:
                payload = {}
            event = {**payload, "pdf_sha256": sha}
            yield f"event: {name}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"

    # Assemble the canonical CodexDocument from the parts we already
    # have — no second pass over the PDF — and cache it under the
    # standard ``extract`` key so plain ``/v1/extract`` requests hit.
    try:
        fitz_data = await loop.run_in_executor(None, _run_fitz_pipeline, raw)
        full = assemble_codex_document(
            raw,
            source_uri=None,
            fitz_data=fitz_data,
            output_intents=output_intents,
            color_spaces=color_spaces,
            ocgs=ocgs,
            form_xobjects=form_xobjects,
            analysis=analysis,
        )
        payload_full = full.model_dump(mode="json")
        body = json.dumps(payload_full, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _cache.set(cache_key(raw, {}, kind="extract"), body)
        out = {**payload_full, "pdf_sha256": sha, "extract_phase": 2}
        yield f"event: phase2_complete\ndata: {json.dumps(out, separators=(',', ':'))}\n\n"
    except Exception:
        logger.exception("granular phase2_complete failed")


@app.post("/v1/extract/stream", dependencies=[Depends(authenticate)])
async def extract_stream_endpoint(
    request: Request,
    pdf: UploadFile | None = File(default=None),
    granular: int = 0,
) -> StreamingResponse:
    """Two-phase SSE extract for minimal time-to-first-data.

    Default mode emits two ``data:`` events:
    - Phase 1 (~200ms): PyMuPDF + 3 pikepdf passes — page dimensions,
      fonts, images, annotations, color spaces, output intents, ocgs,
      form_xobjects.  ``analysis`` is an empty placeholder.
    - Phase 2 (~full extract time): complete CodexDocument with the
      analysis signals merged in.

    Granular mode (``?granular=1``) emits five named events instead:
    ``phase1`` (PyMuPDF only) → ``color_world`` / ``ocgs`` /
    ``form_xobjects`` / ``analysis`` (in completion order) →
    ``phase2_complete`` (full CodexDocument). Lets viewers paint
    individual signals the moment each pass finishes.
    """
    started = time.perf_counter()
    try:
        raw = await _read_extract_pdf(request, pdf)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {exc}") from exc

    sha = hashlib.sha256(raw).hexdigest()
    _blob_store.put(sha, raw)
    loop = asyncio.get_event_loop()
    use_granular = bool(granular)

    async def _generate_two_phase() -> AsyncIterator[str]:
        # Phase 1: PyMuPDF + parallel color/ocgs/forms — sha-keyed cache.
        try:
            p1 = await loop.run_in_executor(None, _run_extract_phase1, raw)
            p1 = {**p1, "pdf_sha256": sha, "extract_phase": 1}
            yield f"data: {json.dumps(p1, separators=(',', ':'))}\n\n"
        except Exception:
            pass  # Phase 2 will still follow

        # Phase 2: full extraction with the analysis pass merged in.
        try:
            p2 = await loop.run_in_executor(None, _run_extract, raw)
            if isinstance(p2, dict):
                p2 = {**p2, "pdf_sha256": sha, "extract_phase": 2}
            yield f"data: {json.dumps(p2, separators=(',', ':'))}\n\n"
        except Exception:
            pass

    async def _generate() -> AsyncIterator[str]:
        if use_granular:
            async for chunk in _generate_granular_stream(raw, sha):
                yield chunk
        else:
            async for chunk in _generate_two_phase():
                yield chunk
        asyncio.ensure_future(loop.run_in_executor(None, _pre_render_bg, raw))
        _record("extract_stream", 200, time.perf_counter() - started)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Render.
# ---------------------------------------------------------------------------


@app.post("/v1/render/page", dependencies=[Depends(authenticate)])
async def render_page_endpoint(
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
    page: int = Form(default=1),
    dpi: int = Form(default=300),
    ocg_on: str | None = Form(default=None),
    ocg_off: str | None = Form(default=None),
    simulate_overprint: bool = Form(default=True),
) -> Response:
    started = time.perf_counter()
    try:
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
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
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
    page: int = Form(default=1),
    dpi: int = Form(default=150),
    tac_limit: float = Form(default=300),
) -> Response:
    started = time.perf_counter()
    try:
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
    page: int = Form(default=1),
    layer_index: int = Form(...),
    all_layer_indices: str = Form(...),
    dpi: int = Form(default=150),
) -> Response:
    started = time.perf_counter()
    try:
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
    page: int = Form(default=1),
    x: float = Form(...),
    y: float = Form(...),
    page_w: float | None = Form(default=None),
    page_h: float | None = Form(default=None),
    dpi: int = Form(default=300),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
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
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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


@app.post("/v1/walk/type4", dependencies=[Depends(authenticate)])
async def walk_type4_endpoint(body: Type4Request) -> Type4Response:
    """Evaluate a PDF Type-4 PostScript function via codex.

    Codex owns the PostScript byte-level evaluation surface; this
    endpoint exists so lint-pdf (and any other consumer) can avoid
    shelling out to ``gs -dNODISPLAY`` directly. Fast-path constants
    are returned synchronously without a subprocess.
    """
    started = time.perf_counter()
    try:
        from codex_pdf.eval.ps_type4 import _fast_path_constants, evaluate

        fast = _fast_path_constants(body.program)
        result = evaluate(body.program, inputs=list(body.inputs))
        _record("walk_type4", 200, time.perf_counter() - started)
        return Type4Response(result=result, fast_path=fast is not None)
    except Exception as exc:
        logger.exception("walk_type4 failed")
        _record("walk_type4", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"walk_type4 failed: {exc}",
        ) from exc


@app.post("/v1/walk/content-stream", dependencies=[Depends(authenticate)])
async def walk_content_stream_endpoint(
    pdf: UploadFile | None = File(default=None),
    pdf_sha256: str | None = Form(default=None),
    page: int = Form(default=1),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        raw, _ = await _resolve_pdf_bytes(pdf, pdf_sha256)
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


# ---------------------------------------------------------------------------
# Color authority. Codex owns the canonical Pantone reference + spot
# resolver; lint and loupe both consume this surface (lint in-process,
# loupe via HTTP) so we never have two forks of the colour-math
# implementation drifting out of sync.
# ---------------------------------------------------------------------------


def _coerce_lab(value: list[float] | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if len(value) != 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"lab must have 3 components, got {len(value)}",
        )
    return (float(value[0]), float(value[1]), float(value[2]))


def _coerce_cmyk(value: list[float] | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if len(value) != 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cmyk must have 4 components, got {len(value)}",
        )
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _coerce_rgb(value: list[int] | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    if len(value) != 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"rgb must have 3 components, got {len(value)}",
        )
    return (int(value[0]), int(value[1]), int(value[2]))


def _resolve_request_to_args(body: ColorResolveRequest) -> dict[str, object]:
    host = body.host_override
    codex_intent = body.codex
    return {
        "host_override": SpotInkOverride(
            rgb=_coerce_rgb(host.rgb) if host else None,
            lab=_coerce_lab(host.lab) if host else None,
            cmyk=_coerce_cmyk(host.cmyk) if host else None,
            pantone_name=host.pantone_name if host else None,
        ) if host is not None else None,
        "codex_intent": CodexSpotIntent(
            rgb=_coerce_rgb(codex_intent.rgb) if codex_intent else None,
            lab=_coerce_lab(codex_intent.lab) if codex_intent else None,
            cmyk=_coerce_cmyk(codex_intent.cmyk) if codex_intent else None,
            pantone_name=codex_intent.pantone_name if codex_intent else None,
        ) if codex_intent is not None else None,
        "extra_pantone_overrides": body.extra_pantone_overrides,
    }


@app.post(
    "/v1/color/resolve",
    response_model=ColorResolveResponse,
    dependencies=[Depends(authenticate)],
)
async def color_resolve_endpoint(body: ColorResolveRequest) -> ColorResolveResponse:
    started = time.perf_counter()
    try:
        args = _resolve_request_to_args(body)
        result = resolve_spot_swatch_color(
            body.name,
            host_override=args["host_override"],  # type: ignore[arg-type]
            codex_intent=args["codex_intent"],  # type: ignore[arg-type]
            extra_pantone_overrides=args["extra_pantone_overrides"],  # type: ignore[arg-type]
        )
        _record("color_resolve", 200, time.perf_counter() - started)
        return ColorResolveResponse(
            schema_version=COLOR_SCHEMA_VERSION,
            rgb=list(result.rgb),
            source=result.source,
            lab=list(result.lab) if result.lab is not None else None,
            cmyk=list(result.cmyk) if result.cmyk is not None else None,
            pantone_name=result.pantone_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("color_resolve failed")
        _record("color_resolve", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"color_resolve failed: {exc}",
        ) from exc


def _measurement_to_lab(body: ColorMatchPantoneRequest) -> tuple[float, float, float]:
    lab = _coerce_lab(body.lab)
    if lab is not None:
        return lab
    cmyk = _coerce_cmyk(body.cmyk)
    if cmyk is not None:
        rgb = lab_d50_to_srgb(_cmyk_to_lab_via_srgb(cmyk))
        return _srgb_to_lab(rgb)
    rgb = _coerce_rgb(body.rgb)
    if rgb is not None:
        return _srgb_to_lab(rgb)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="match-pantone requires one of {lab, cmyk, rgb}",
    )


def _srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert an 8-bit sRGB triplet to Lab D50 via XYZ.

    Used only inside ``/v1/color/match-pantone`` so a request that
    arrives as RGB or CMYK can still find a Pantone neighbour. The
    conversion mirrors :func:`codex_pdf.color.color_math.lab_d50_to_srgb`'s
    matrices in reverse so round-trips are stable.
    """
    from codex_pdf.color.color_math import (
        D50_TO_D65,
        D50_WHITE,
        XYZ_D65_FROM_LINEAR_SRGB,
    )

    r_lin = srgb_decode(rgb[0] / 255.0)
    g_lin = srgb_decode(rgb[1] / 255.0)
    b_lin = srgb_decode(rgb[2] / 255.0)
    x65 = (
        XYZ_D65_FROM_LINEAR_SRGB[0][0] * r_lin
        + XYZ_D65_FROM_LINEAR_SRGB[0][1] * g_lin
        + XYZ_D65_FROM_LINEAR_SRGB[0][2] * b_lin
    )
    y65 = (
        XYZ_D65_FROM_LINEAR_SRGB[1][0] * r_lin
        + XYZ_D65_FROM_LINEAR_SRGB[1][1] * g_lin
        + XYZ_D65_FROM_LINEAR_SRGB[1][2] * b_lin
    )
    z65 = (
        XYZ_D65_FROM_LINEAR_SRGB[2][0] * r_lin
        + XYZ_D65_FROM_LINEAR_SRGB[2][1] * g_lin
        + XYZ_D65_FROM_LINEAR_SRGB[2][2] * b_lin
    )
    # Inverse of D50→D65 to recover D50 XYZ.
    inv = _invert_3x3(D50_TO_D65)
    x50 = inv[0][0] * x65 + inv[0][1] * y65 + inv[0][2] * z65
    y50 = inv[1][0] * x65 + inv[1][1] * y65 + inv[1][2] * z65
    z50 = inv[2][0] * x65 + inv[2][1] * y65 + inv[2][2] * z65
    fx = _lab_f(x50 / D50_WHITE[0])
    fy = _lab_f(y50 / D50_WHITE[1])
    fz = _lab_f(z50 / D50_WHITE[2])
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return (L, a, b)


def _lab_f(t: float) -> float:
    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    if t > eps:
        return t ** (1.0 / 3.0)
    return (kappa * t + 16.0) / 116.0


def _invert_3x3(
    m: tuple[tuple[float, float, float], ...]
) -> tuple[tuple[float, float, float], ...]:
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-15:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="chromatic-adaptation matrix is singular",
        )
    inv_det = 1.0 / det
    return (
        ((e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det),
        ((f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det),
        ((d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det),
    )


def _cmyk_to_lab_via_srgb(
    cmyk: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    """Naïve CMYK→Lab via sRGB. Approximate; matches resolver maths."""
    from codex_pdf.color.color_math import cmyk_to_srgb_naive

    rgb = cmyk_to_srgb_naive(cmyk)
    return _srgb_to_lab(rgb)


@app.post(
    "/v1/color/match-pantone",
    response_model=ColorMatchPantoneResponse,
    dependencies=[Depends(authenticate)],
)
async def color_match_pantone_endpoint(
    body: ColorMatchPantoneRequest,
) -> ColorMatchPantoneResponse:
    started = time.perf_counter()
    try:
        lab = _measurement_to_lab(body)
        ref = load_pantone_reference()
        nearest = match_nearest_pantone(lab, reference=ref, libraries=body.libraries)
        if nearest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="no Pantone entries matched the requested library filter",
            )
        entry, de = nearest
        _record("color_match_pantone", 200, time.perf_counter() - started)
        return ColorMatchPantoneResponse(
            schema_version=COLOR_SCHEMA_VERSION,
            pantone_name=entry.name,
            library=entry.library,
            delta_e=de,
            lab=list(entry.lab) if entry.lab is not None else list(lab),
            cmyk=list(entry.cmyk_bridge) if entry.cmyk_bridge is not None else None,
            rgb=list(lab_d50_to_srgb(entry.lab)) if entry.lab is not None else [0, 0, 0],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("color_match_pantone failed")
        _record("color_match_pantone", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"color_match_pantone failed: {exc}",
        ) from exc


@app.get("/v1/color/inkbook")
async def color_inkbook_endpoint(
    libraries: str | None = None,
    _: object = Depends(authenticate),
) -> JSONResponse:
    started = time.perf_counter()
    try:
        libs = (
            [s.strip() for s in libraries.split(",") if s.strip()]
            if libraries is not None
            else None
        )
        payload = load_inkbook(libraries=libs)
        _record("color_inkbook", 200, time.perf_counter() - started)
        return JSONResponse(payload)
    except Exception as exc:
        logger.exception("color_inkbook failed")
        _record("color_inkbook", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"color_inkbook failed: {exc}",
        ) from exc


def _nd_from_lab(lab: tuple[float, float, float]) -> float:
    """Compute neutral density from CIE L* using the luminance relationship.

    ND = -log10(Y_rel), where Y_rel = ((L* + 16) / 116)^3 per CIELAB.
    """
    L = max(0.0, min(100.0, lab[0]))
    Y_rel = max(1e-7, ((L + 16.0) / 116.0) ** 3)
    return round(-math.log10(Y_rel), 4)


@app.post(
    "/v1/color/neutral-density",
    response_model=NeutralDensityResponse,
    dependencies=[Depends(authenticate)],
)
async def color_neutral_density_endpoint(
    body: NeutralDensityRequest,
) -> NeutralDensityResponse:
    """Return the neutral density for a spot colorant.

    Accepts ``name`` (resolved via spot-color ladder), ``lab`` (CIE Lab D50),
    or ``cmyk`` (0–100 quad). Lab is the most accurate; CMYK uses the naïve
    linearisation. If resolved from a named Pantone entry whose Lab is known
    the source is ``computed_from_lab``; for hash-resolved colours it is
    ``estimated``.
    """
    started = time.perf_counter()
    try:
        lab: tuple[float, float, float] | None = None
        source: str = "computed_from_lab"

        if body.lab is not None:
            lab = _coerce_lab(body.lab)
            source = "computed_from_lab"
        elif body.cmyk is not None:
            cmyk = _coerce_cmyk(body.cmyk)
            lab = _cmyk_to_lab_via_srgb(cmyk)  # type: ignore[arg-type]
            source = "estimated"
        elif body.name is not None:
            result = resolve_spot_swatch_color(body.name)
            if result.lab is not None:
                lab = result.lab
                source = "computed_from_lab" if result.pantone_name else "estimated"
            else:
                # Fall back via RGB → Lab
                rgb = result.rgb
                lab = _srgb_to_lab(rgb)
                source = "estimated"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="neutral-density requires one of {name, lab, cmyk}",
            )

        nd = _nd_from_lab(lab)  # type: ignore[arg-type]
        _record("color_neutral_density", 200, time.perf_counter() - started)
        return NeutralDensityResponse(
            schema_version=COLOR_SCHEMA_VERSION,
            neutral_density=nd,
            source=source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("color_neutral_density failed")
        _record("color_neutral_density", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"color_neutral_density failed: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Geometry primitives. Pure-data — no PDF emit. Useful for imposition
# previews, layer-bbox math, and trap-zone planning before any
# producer service exists.
# ---------------------------------------------------------------------------


@app.post(
    "/v1/geom/tile",
    response_model=GeomTileResponse,
    dependencies=[Depends(authenticate)],
)
async def geom_tile_endpoint(body: GeomTileRequest) -> GeomTileResponse:
    started = time.perf_counter()
    try:
        sheet_box = GeomBox(
            body.sheet.x0, body.sheet.y0, body.sheet.x1, body.sheet.y1
        )
        if sheet_box.empty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sheet is empty",
            )
        marks = MarksZone(
            top=body.marks_zone.top,
            right=body.marks_zone.right,
            bottom=body.marks_zone.bottom,
            left=body.marks_zone.left,
        )
        # Convert list[list[float]] → tuple[tuple[float,...],...]  for dataclass
        rot_pat = (
            tuple(tuple(row) for row in body.cell_rotation_pattern)
            if body.cell_rotation_pattern is not None
            else None
        )
        flip_pat = (
            tuple(tuple(row) for row in body.flip_pattern)
            if body.flip_pattern is not None
            else None
        )
        result = tile_grid(
            TileGrid(
                sheet=sheet_box,
                cell_width=body.cell_width,
                cell_height=body.cell_height,
                gutter_x=body.gutter_x,
                gutter_y=body.gutter_y,
                marks_zone=marks,
                origin=body.origin,
                cell_rotation=body.cell_rotation,
                cell_rotation_pattern=rot_pat,
                flip_per_row=body.flip_per_row,
                flip_pattern=flip_pat,
                bleed_handling=body.bleed_handling,  # type: ignore[arg-type]
                bleed=body.bleed,
            )
        )
        _record("geom_tile", 200, time.perf_counter() - started)
        return GeomTileResponse(
            schema_version=GEOM_SCHEMA_VERSION,
            rows=result.rows,
            cols=result.cols,
            cells=[cell.box.to_list() for cell in result.cells],
            placements=[
                CellPlacementModel(
                    box=cell.box.to_list(),
                    rotation=cell.rotation,
                    flip_h=cell.flip_h,
                    flip_v=cell.flip_v,
                    row=cell.row,
                    col=cell.col,
                )
                for cell in result.cells
            ],
            used=result.used.to_list(),
            waste=result.waste.to_list(),
        )
    except ValueError as exc:
        _record("geom_tile", 400, time.perf_counter() - started)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("geom_tile failed")
        _record("geom_tile", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geom_tile failed: {exc}",
        ) from exc


def _paths_from_payload(
    payload: list[list[list[list[float]]]] | None,
) -> list[GeomPath]:
    if not payload:
        return []
    out: list[GeomPath] = []
    for raw_path in payload:
        out.append(GeomPath.from_json(raw_path))
    return out


def _run_boolean(op: str, body: GeomBooleanRequest) -> GeomPath:
    subjects = _paths_from_payload(body.subjects)
    clips = _paths_from_payload(body.clips)
    if not subjects:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one subject path is required",
        )
    if op == "intersect":
        if not clips:
            return polygon_intersect(*subjects)
        result = subjects[0]
        for other in list(subjects[1:]) + clips:
            result = polygon_intersect(result, other)
        return result
    if op == "union":
        return polygon_union(*subjects, *clips)
    if op == "difference":
        if not clips:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="difference requires at least one clip path",
            )
        result = subjects[0]
        for clip in clips:
            result = polygon_difference(result, clip)
        return result
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"unknown geom op: {op}",
    )


@app.post(
    "/v1/geom/intersect",
    response_model=GeomBooleanResponse,
    dependencies=[Depends(authenticate)],
)
async def geom_intersect_endpoint(body: GeomBooleanRequest) -> GeomBooleanResponse:
    started = time.perf_counter()
    try:
        result = _run_boolean("intersect", body)
        _record("geom_intersect", 200, time.perf_counter() - started)
        return GeomBooleanResponse(
            schema_version=GEOM_SCHEMA_VERSION,
            rings=[[[p[0], p[1]] for p in ring] for ring in result.rings],
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        _record("geom_intersect", 501, time.perf_counter() - started)
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("geom_intersect failed")
        _record("geom_intersect", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geom_intersect failed: {exc}",
        ) from exc


@app.post(
    "/v1/geom/union",
    response_model=GeomBooleanResponse,
    dependencies=[Depends(authenticate)],
)
async def geom_union_endpoint(body: GeomBooleanRequest) -> GeomBooleanResponse:
    started = time.perf_counter()
    try:
        result = _run_boolean("union", body)
        _record("geom_union", 200, time.perf_counter() - started)
        return GeomBooleanResponse(
            schema_version=GEOM_SCHEMA_VERSION,
            rings=[[[p[0], p[1]] for p in ring] for ring in result.rings],
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        _record("geom_union", 501, time.perf_counter() - started)
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("geom_union failed")
        _record("geom_union", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geom_union failed: {exc}",
        ) from exc


@app.post(
    "/v1/geom/difference",
    response_model=GeomBooleanResponse,
    dependencies=[Depends(authenticate)],
)
async def geom_difference_endpoint(body: GeomBooleanRequest) -> GeomBooleanResponse:
    started = time.perf_counter()
    try:
        result = _run_boolean("difference", body)
        _record("geom_difference", 200, time.perf_counter() - started)
        return GeomBooleanResponse(
            schema_version=GEOM_SCHEMA_VERSION,
            rings=[[[p[0], p[1]] for p in ring] for ring in result.rings],
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        _record("geom_difference", 501, time.perf_counter() - started)
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("geom_difference failed")
        _record("geom_difference", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geom_difference failed: {exc}",
        ) from exc


@app.post(
    "/v1/geom/offset",
    response_model=GeomOffsetResponse,
    dependencies=[Depends(authenticate)],
)
async def geom_offset_endpoint(body: GeomOffsetRequest) -> GeomOffsetResponse:
    """Offset (spread/choke) a polygon path by ``distance_pt`` points.

    Positive distance grows (spread); negative shrinks (choke). Uses
    pyclipr (Clipper2) when installed, with a rectangle fast-path
    fallback for axis-aligned rectangles when pyclipr is absent.
    """
    started = time.perf_counter()
    try:
        path = GeomPath.from_json(body.path)
        result = polygon_offset(
            path,
            body.distance_pt,
            join_type=body.join_type,
            end_type=body.end_type,
            miter_limit=body.miter_limit,
        )
        _record("geom_offset", 200, time.perf_counter() - started)
        return GeomOffsetResponse(
            schema_version=GEOM_SCHEMA_VERSION,
            rings=[[[p[0], p[1]] for p in ring] for ring in result.rings],
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        _record("geom_offset", 501, time.perf_counter() - started)
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("geom_offset failed")
        _record("geom_offset", 500, time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geom_offset failed: {exc}",
        ) from exc
