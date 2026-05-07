"""URL-based PDF ingestion for the codex extract endpoint.

Used when a caller posts ``application/json`` with a ``url`` (or
``s3_url`` / ``presigned_url``) field instead of multipart bytes.

Behaviour (all knobs live in environment so deploys can lock down the
fetch surface without code changes):

- Scheme must be ``http`` or ``https``.
- Streamed download with hard size cap (``FETCH_MAX_BYTES``,
  default 50 MiB).
- Hard wall-clock timeout (``FETCH_TIMEOUT_MS``, default 15s).
- Validates ``Content-Type`` is ``application/pdf`` OR the URL path
  ends in ``.pdf``.
- Validates first 5 bytes are ``%PDF-`` magic before returning.
- Gated by ``ALLOW_EXTERNAL_FETCH`` (default false). When false the
  endpoint returns 400 instead of fetching, so a misconfigured public
  deployment cannot become an open SSRF tarpit by accident.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


PDF_MAGIC = b"%PDF-"


def fetch_enabled() -> bool:
    """Return True iff URL ingestion is allowed in this deployment."""
    raw = os.environ.get("ALLOW_EXTERNAL_FETCH", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _max_bytes() -> int:
    raw = os.environ.get("FETCH_MAX_BYTES", "")
    if not raw:
        return 50 * 1024 * 1024
    try:
        value = int(raw)
    except ValueError:
        return 50 * 1024 * 1024
    return max(1, value)


def _timeout_seconds() -> float:
    raw = os.environ.get("FETCH_TIMEOUT_MS", "")
    if not raw:
        return 15.0
    try:
        value = int(raw)
    except ValueError:
        return 15.0
    return max(1.0, value / 1000.0)


def _looks_like_pdf_path(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".pdf")


def fetch_pdf_from_url(url: str) -> bytes:
    """Download ``url`` and return PDF bytes.

    Raises:
        HTTPException: with the right status / detail combination so
            the caller can re-raise unchanged.
    """
    if not fetch_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL ingestion is disabled (ALLOW_EXTERNAL_FETCH=false)",
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported scheme: {parsed.scheme!r}",
        )
    if not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing host in url",
        )

    max_bytes = _max_bytes()
    timeout = _timeout_seconds()

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "codex-pdf/1.2.0", "Accept": "application/pdf"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            content_length_raw = resp.headers.get("Content-Length")
            if content_length_raw:
                try:
                    declared = int(content_length_raw)
                except ValueError:
                    declared = 0
                if declared > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"declared Content-Length {declared} exceeds FETCH_MAX_BYTES {max_bytes}"
                        ),
                    )

            chunks: list[bytes] = []
            received = 0
            magic_checked = False
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                if not magic_checked:
                    if len(chunk) < len(PDF_MAGIC):
                        # Need more bytes before we can validate magic.
                        # Fall through; below loop iteration will accumulate.
                        pass
                    else:
                        head = (b"".join(chunks) + chunk)[: len(PDF_MAGIC)]
                        if head and head != PDF_MAGIC:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="downloaded body is not a PDF (missing %PDF- magic)",
                            )
                        magic_checked = True
                received += len(chunk)
                if received > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"download exceeded FETCH_MAX_BYTES={max_bytes} "
                            f"(received {received} bytes)"
                        ),
                    )
                chunks.append(chunk)

            body = b"".join(chunks)
            if not body:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="downloaded body was empty",
                )

            if (
                content_type
                and content_type != "application/pdf"
                and content_type != "application/octet-stream"
                and not _looks_like_pdf_path(url)
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"unexpected content-type {content_type!r}; "
                        "url must serve application/pdf or end in .pdf"
                    ),
                )

            if body[: len(PDF_MAGIC)] != PDF_MAGIC:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="downloaded body is not a PDF (missing %PDF- magic)",
                )

            return body
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"upstream returned HTTP {exc.code} fetching {url}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failed to fetch url: {exc.reason}",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"fetch timed out after {timeout:.1f}s",
        ) from exc
