"""HTTP client used by the main codex-pdf API to call into the
vision sidecar.

The client is intentionally minimal: one method per remote
extractor, all of them degrade to a typed empty result when
``CODEX_VISION_URL`` is unset or the sidecar is unreachable. The
main API surfaces the degraded state through the
``vision_unavailable`` warning so consumers can tell vision-empty
from vision-absent.
"""

from __future__ import annotations

import logging
import os
from typing import Final

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S: Final[float] = 8.0


def _base_url() -> str | None:
    raw = (os.environ.get("CODEX_VISION_URL") or "").strip().rstrip("/")
    return raw or None


def _auth_header() -> dict[str, str]:
    token = os.environ.get("CODEX_INTERNAL_TOKEN")
    if not token:
        return {}
    return {"X-Codex-Internal-Token": token}


def is_configured() -> bool:
    """True when ``CODEX_VISION_URL`` is set on this deployment."""
    return _base_url() is not None


def compute_phash(png_bytes: bytes) -> str | None:
    """Forward a PNG to the vision sidecar and return its 64-bit
    perceptual hash. Returns ``None`` on misconfiguration / error;
    the caller should emit ``vision_unavailable`` on a None response.
    """
    base = _base_url()
    if not base:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            response = client.post(
                f"{base}/v1/vision/phash",
                headers=_auth_header(),
                files={"image": ("page.png", png_bytes, "image/png")},
            )
    except httpx.HTTPError:
        logger.exception("vision sidecar unreachable")
        return None
    if response.status_code != 200:
        logger.warning(
            "vision sidecar phash HTTP %s: %s",
            response.status_code,
            response.text[:200],
        )
        return None
    payload = response.json()
    hash_hex = payload.get("hash")
    return hash_hex if isinstance(hash_hex, str) else None


def healthcheck() -> bool:
    """Return True when the sidecar's /healthz reports ok.

    Called from codex's /healthz to surface the vision lane's
    availability so operators can detect a missing or broken sidecar
    without grep'ing the request logs.
    """
    base = _base_url()
    if not base:
        return False
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            response = client.get(f"{base}/healthz", headers=_auth_header())
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        return bool(response.json().get("ok"))
    except ValueError:
        return False
