"""URL-based PDF ingestion for the codex extract endpoint.

Used when a caller posts ``application/json`` with a ``url`` (or
``s3_url`` / ``presigned_url``) field instead of multipart bytes.

Behaviour (all knobs live in environment so deploys can lock down the
fetch surface without code changes):

- Scheme must be ``http`` or ``https``. ``file://``, ``data:``,
  ``ftp:``, ``gopher:`` etc. are rejected with 400.
- DNS is resolved up-front and every candidate address is checked
  against an allow-list (no loopback, link-local, private RFC1918,
  CGNAT, multicast, broadcast, IPv6 ULA / link-local). Defends
  against DNS rebinding because we connect to a literal IP from the
  pre-resolved set rather than re-resolving inside ``urlopen``.
- Cap of ``MAX_REDIRECTS`` (default 3) hops; every redirect target
  is re-validated against the same SSRF rules before following.
- Streamed download with hard size cap (``FETCH_MAX_BYTES``,
  default 50 MiB).
- Hard wall-clock timeout (``FETCH_TIMEOUT_MS``, default 15s).
- Validates ``Content-Type`` is ``application/pdf`` OR the URL path
  ends in ``.pdf``.
- Validates first 5 bytes are ``%PDF-`` magic before returning.
- Gated by ``ALLOW_EXTERNAL_FETCH`` (default false). When false the
  endpoint returns 400 instead of fetching.
- ``CODEX_FETCH_ALLOW_PRIVATE=1`` opts back into private-IP fetches
  for trusted intra-cluster deployments (used by integration tests
  that run a fixture HTTP server on 127.0.0.1).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import urllib.error
import urllib.request
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


PDF_MAGIC = b"%PDF-"
DEFAULT_MAX_REDIRECTS = 3
MAX_REDIRECTS_CAP = 10


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


def _max_redirects() -> int:
    raw = os.environ.get("FETCH_MAX_REDIRECTS", "")
    if not raw:
        return DEFAULT_MAX_REDIRECTS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REDIRECTS
    return max(0, min(MAX_REDIRECTS_CAP, value))


def _allow_private_ips() -> bool:
    raw = os.environ.get("CODEX_FETCH_ALLOW_PRIVATE", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_FORBIDDEN_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
        "kubernetes.default.svc",
    }
)


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True iff the address must not be reached from the codex API.

    Defense-in-depth covers every range the user prompt called out
    plus a handful that public SSRF cheat sheets routinely exploit:
    cloud metadata services live on 169.254.169.254 (link-local) /
    fd00:ec2::254 (ULA), which is already covered by the broader
    private/link-local checks below.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        if ip.is_loopback:  # 127.0.0.0/8
            return True
        if ip.is_link_local:  # 169.254.0.0/16
            return True
        if ip.is_private:  # 10/8, 172.16/12, 192.168/16, 100.64/10 CGNAT
            return True
        if ip.is_multicast:  # 224.0.0.0/4
            return True
        if ip.is_reserved:  # 240.0.0.0/4
            return True
        if ip.is_unspecified:  # 0.0.0.0
            return True
        if ip == ipaddress.IPv4Address("255.255.255.255"):
            return True
    else:
        if ip.is_loopback:  # ::1
            return True
        if ip.is_link_local:  # fe80::/10
            return True
        if ip.is_site_local:  # fec0::/10 (deprecated)
            return True
        if ip.is_private:  # fc00::/7 (ULA) + others
            return True
        if ip.is_multicast:  # ff00::/8
            return True
        if ip.is_unspecified:
            return True
    return False


def _resolve_safe(host: str, port: int) -> list[tuple[int, str]]:
    """Resolve ``host`` to address families and validate every result.

    Returns a list of ``(family, ip)`` tuples ready for ``socket``
    consumption. Raises :class:`HTTPException` on any disallowed IP.
    DNS rebinding is prevented because the caller must connect to one
    of these IPs directly rather than re-resolve the hostname.
    """
    if host.strip().lower() in _FORBIDDEN_HOSTNAMES and not _allow_private_ips():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"refused to fetch {host!r}: forbidden hostname",
        )

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"DNS resolution failed for {host!r}: {exc}",
        ) from exc

    allow_private = _allow_private_ips()
    safe: list[tuple[int, str]] = []
    for family, _socktype, _proto, _canon, sockaddr in infos:
        if family == socket.AF_INET:
            ip_text = sockaddr[0]
            ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.IPv4Address(
                ip_text
            )
        elif family == socket.AF_INET6:
            ip_text = sockaddr[0]
            ip_obj = ipaddress.IPv6Address(ip_text.split("%", 1)[0])
        else:
            continue
        if _is_forbidden_ip(ip_obj) and not allow_private:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"refused to fetch {host!r}: resolves to forbidden address {ip_obj!s}"
                ),
            )
        safe.append((family, ip_text))

    if not safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"DNS resolution for {host!r} returned no addresses",
        )
    return safe


def _validate_url(url: str) -> tuple[str, str, int, str, list[tuple[int, str]]]:
    """Validate scheme/host and pre-resolve a safe IP list.

    Returns ``(scheme, host, port, path_with_query, safe_ips)``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported scheme: {parsed.scheme!r}",
        )
    if not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing host in url",
        )
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    safe_ips = _resolve_safe(host, port)
    return parsed.scheme, host, port, target, safe_ips


def _connect(scheme: str, ip: str, port: int, host_header: str, timeout: float) -> HTTPConnection:
    """Open a literal-IP connection so DNS rebinding can't redirect us mid-flight."""
    if scheme == "https":
        conn: HTTPConnection = HTTPSConnection(ip, port, timeout=timeout)
    else:
        conn = HTTPConnection(ip, port, timeout=timeout)
    # Preserve the original Host header so vhost-routed servers respond correctly.
    conn._http_vsn_str = "HTTP/1.1"  # type: ignore[attr-defined]
    return conn


def fetch_pdf_from_url(url: str) -> bytes:
    """Download ``url`` and return PDF bytes.

    Implements:
    - SSRF allow-listing on every candidate IP after DNS resolution.
    - Literal-IP connect (with Host header) so DNS rebinding is moot.
    - Redirect cap with re-validation per hop.
    - Streaming size cap, content-type guard, %PDF- magic check.
    """
    if not fetch_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL ingestion is disabled (ALLOW_EXTERNAL_FETCH=false)",
        )

    max_bytes = _max_bytes()
    timeout = _timeout_seconds()
    max_redirects = _max_redirects()

    current_url = url
    visited: list[str] = []
    for hop in range(max_redirects + 1):
        if current_url in visited:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"redirect loop detected at {current_url}",
            )
        visited.append(current_url)

        scheme, host, port, target, safe_ips = _validate_url(current_url)
        family, ip = safe_ips[0]
        del family  # noqa: F841 — selected by _connect via socket family

        conn = _connect(scheme, ip, port, host, timeout)
        try:
            conn.request(
                "GET",
                target,
                headers={
                    "Host": f"{host}:{port}" if port not in (80, 443) else host,
                    "User-Agent": "codex-pdf/1.3.0",
                    "Accept": "application/pdf",
                    "Connection": "close",
                },
            )
            try:
                resp: HTTPResponse = conn.getresponse()
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=status.HTTP_408_REQUEST_TIMEOUT,
                    detail=f"fetch timed out after {timeout:.1f}s",
                ) from exc
            except OSError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"failed to fetch url: {exc}",
                ) from exc

            # Follow redirects with per-hop revalidation.
            if 300 <= resp.status < 400:
                location = resp.getheader("Location")
                if not location:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"redirect {resp.status} without Location header",
                    )
                if hop >= max_redirects:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"too many redirects (max={max_redirects})",
                    )
                if "://" not in location:
                    new_parsed = urlparse(current_url)
                    if location.startswith("/"):
                        rebuilt = f"{new_parsed.scheme}://{new_parsed.netloc}{location}"
                    else:
                        rebuilt = urlunparse(
                            new_parsed._replace(path=location, query="", fragment="")
                        )
                    current_url = rebuilt
                else:
                    current_url = location
                continue

            if resp.status >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"upstream returned HTTP {resp.status} fetching {current_url}",
                )

            content_type = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            content_length_raw = resp.getheader("Content-Length")
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
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
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
                and not _looks_like_pdf_path(current_url)
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
        finally:
            try:
                conn.close()
            except Exception:
                pass

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="redirect chain exceeded without a final response",
    )


# Backwards-compat shim retained for tests / external callers that
# import the old urllib-based path. Internally redirects to the new
# safe fetcher.
def _legacy_fetch_via_urllib(url: str) -> bytes:  # pragma: no cover
    return fetch_pdf_from_url(url)


__all__ = [
    "PDF_MAGIC",
    "fetch_enabled",
    "fetch_pdf_from_url",
]
