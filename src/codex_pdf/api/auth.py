"""Codex API auth.

Supports four coexisting modes — operators turn on whichever fit
their deployment. Any single passing mode authenticates the call.

- ``none`` (default in dev) — auth header optional.
- ``bearer`` — ``Authorization: Bearer <CODEX_BEARER_TOKEN>``.
- ``api-key`` — ``X-Codex-Key: <CODEX_API_KEY>``.
- ``internal`` — ``X-Codex-Internal: <CODEX_INTERNAL_TOKEN>`` for
  service-to-service sidecar calls.
- ``basic`` — HTTP Basic Auth gated by
  ``CODEX_BASIC_AUTH_ENABLED=true`` plus
  ``CODEX_BASIC_AUTH_USERNAME``/``CODEX_BASIC_AUTH_PASSWORD``.
  Constant-time compare. Useful for browser-side curl probes during
  the Railway demo cutover.

``CODEX_AUTH_MODE`` (optional, comma-separated) explicitly locks the
allow-list — e.g. ``CODEX_AUTH_MODE=bearer,basic``. Leave it unset to
let the auto-detector pick every mode whose secret is configured.
"""

from __future__ import annotations

import base64
import hmac
import os

from fastapi import Header, HTTPException, status


def _split_modes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [m.strip().lower() for m in raw.split(",") if m.strip()]


def _basic_enabled() -> bool:
    raw = os.environ.get("CODEX_BASIC_AUTH_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _allowed_modes() -> list[str]:
    explicit = _split_modes(os.environ.get("CODEX_AUTH_MODE"))
    if explicit:
        return explicit
    inferred: list[str] = []
    if os.environ.get("CODEX_BEARER_TOKEN"):
        inferred.append("bearer")
    if os.environ.get("CODEX_API_KEY"):
        inferred.append("api-key")
    if os.environ.get("CODEX_INTERNAL_TOKEN"):
        inferred.append("internal")
    if _basic_enabled() and (
        os.environ.get("CODEX_BASIC_AUTH_USERNAME")
        or os.environ.get("CODEX_BASIC_AUTH_PASSWORD")
    ):
        inferred.append("basic")
    return inferred or ["none"]


def auth_required() -> bool:
    """Return True iff at least one authenticated mode is configured."""
    modes = _allowed_modes()
    return any(m != "none" for m in modes)


def _bearer_ok(authorization: str | None) -> bool:
    expected = os.environ.get("CODEX_BEARER_TOKEN")
    if not expected or not authorization:
        return False
    if not authorization.lower().startswith("bearer "):
        return False
    presented = authorization[7:].strip()
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _api_key_ok(value: str | None) -> bool:
    expected = os.environ.get("CODEX_API_KEY")
    if not expected or not value:
        return False
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def _internal_ok(value: str | None) -> bool:
    expected = os.environ.get("CODEX_INTERNAL_TOKEN")
    if not expected or not value:
        return False
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def _basic_ok(authorization: str | None) -> bool:
    """Validate ``Authorization: Basic <b64(user:pass)>``.

    Constant-time compare on both halves so a wrong username can't be
    timed apart from a wrong password.
    """
    if not _basic_enabled() or not authorization:
        return False
    if not authorization.lower().startswith("basic "):
        return False
    raw = authorization[6:].strip()
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8", errors="replace")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, _, pw = decoded.partition(":")
    expected_user = os.environ.get("CODEX_BASIC_AUTH_USERNAME", "")
    expected_pw = os.environ.get("CODEX_BASIC_AUTH_PASSWORD", "")
    if not expected_user or not expected_pw:
        return False
    user_ok = hmac.compare_digest(user.encode("utf-8"), expected_user.encode("utf-8"))
    pw_ok = hmac.compare_digest(pw.encode("utf-8"), expected_pw.encode("utf-8"))
    return user_ok and pw_ok


async def authenticate(
    authorization: str | None = Header(default=None),
    x_codex_key: str | None = Header(default=None, alias="X-Codex-Key"),
    x_codex_internal: str | None = Header(default=None, alias="X-Codex-Internal"),
) -> None:
    """FastAPI dependency: enforce CODEX_AUTH_MODE.

    Returns None on success; raises 401 with ``WWW-Authenticate``
    challenge headers so curl / browsers prompt correctly.
    """
    if not auth_required():
        return None
    modes = _allowed_modes()
    if "bearer" in modes and _bearer_ok(authorization):
        return None
    if "basic" in modes and _basic_ok(authorization):
        return None
    if "api-key" in modes and _api_key_ok(x_codex_key):
        return None
    if "internal" in modes and _internal_ok(x_codex_internal):
        return None

    challenge_parts: list[str] = []
    if "basic" in modes:
        challenge_parts.append('Basic realm="codex"')
    if "bearer" in modes:
        challenge_parts.append("Bearer")
    headers = {"WWW-Authenticate": ", ".join(challenge_parts)} if challenge_parts else None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="codex auth failed",
        headers=headers,
    )
