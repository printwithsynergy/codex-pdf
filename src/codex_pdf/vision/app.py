"""codex-vision-sidecar FastAPI app.

Exposes the CPU vision extractors over HTTP for the main codex-pdf
API to consume. Deployed as a separate Railway service so codex
deployments can adopt the vision lane without rebuilding the main
service image.

Wire shape:

- ``GET  /healthz``             → ``{"ok": true, "schema_version": "..."}``
- ``POST /v1/vision/phash``     → multipart PNG → ``{"algorithm", "hash"}``
- ``GET  /v1/contract``         → endpoint inventory + schema versions

The main codex API authenticates against this service with the
shared ``CODEX_INTERNAL_TOKEN``; no public ingress is required and
in production the service runs on the Railway private network.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from codex_pdf.version import VERSION
from codex_pdf.vision import VISION_SCHEMA_VERSION
from codex_pdf.vision.phash import PHASH_ALGORITHM, compute_phash

logger = logging.getLogger(__name__)

app = FastAPI(
    title="codex-vision-sidecar",
    version=VERSION,
    description=(
        "CPU-only computer-vision extractors that complement codex-pdf's "
        "AI signal lane. Runs as a separate Railway service so the main "
        "codex API doesn't carry ONNX Runtime in its base image. "
        "Optional: codex degrades gracefully when CODEX_VISION_URL is "
        "unset on the main service."
    ),
)


_MAX_RASTER_BYTES = 50 * 1024 * 1024  # 50 MiB; matches codex's extract cap


def _authenticate(request: Request) -> None:
    expected = os.environ.get("CODEX_INTERNAL_TOKEN")
    if not expected:
        # No token configured → no auth gate. This matches codex-pdf's
        # behaviour on a fresh deployment so operators can smoke-test
        # before wiring secrets.
        return
    sent = request.headers.get("x-codex-internal-token") or ""
    if sent != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid x-codex-internal-token",
        )


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe + version pinout.

    Used by codex-pdf's main API to decide whether the vision lane
    is available; an unhealthy sidecar means codex falls back to
    empty vision-sourced signals with a ``vision_unavailable``
    warning.
    """
    return {
        "ok": True,
        "package_version": VERSION,
        "schema_version": VISION_SCHEMA_VERSION,
        "extractors": {
            "phash": True,
        },
    }


@app.get("/v1/contract")
def contract() -> dict[str, Any]:
    return {
        "contract_name": "codex-vision",
        "schema_version": VISION_SCHEMA_VERSION,
        "package_version": VERSION,
        "endpoints": [
            "GET  /healthz",
            "POST /v1/vision/phash",
            "GET  /v1/contract",
        ],
    }


@app.post("/v1/vision/phash")
async def vision_phash_endpoint(
    request: Request,
    image: UploadFile = File(...),
) -> JSONResponse:
    """Compute the perceptual hash of a single image upload.

    Input: a PNG (or any Pillow-readable raster) under
    ``image`` form field. Output: 64-bit pHash hex + the algorithm
    identifier so consumers can pin against the hash semantics.
    """
    _authenticate(request)
    raw = await image.read()
    if len(raw) > _MAX_RASTER_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"raster exceeds {_MAX_RASTER_BYTES} bytes",
        )
    hash_hex = compute_phash(raw)
    if hash_hex is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="pHash extractor failed (Pillow/imagehash unavailable or input not an image)",
        )
    return JSONResponse({"algorithm": PHASH_ALGORITHM, "hash": hash_hex})
