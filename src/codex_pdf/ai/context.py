"""AI signal request context (gate decision + budget).

Codex's AI signal lane is governed by two switches:

- ``CODEX_AI_ENABLED`` (operator): default off
- ``X-Codex-Skip-AI`` (caller): default off

The combined decision is computed once at the start of each request
and passed down to extractors via :class:`AiContext`. Extractors must
NOT re-read environment variables or request headers — that would
race against an operator config change mid-request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from codex_pdf.ai.budget import AiBudget

AiStatus = Literal[
    "enabled", "disabled", "skipped", "missing_credentials", "tenant_excluded"
]


@dataclass
class AiContext:
    """Snapshot of the AI gate decision for one request.

    ``status``:
    - ``"enabled"`` — codex is allowed to call AI extractors
    - ``"disabled"`` — operator gate is off
    - ``"skipped"`` — caller opted out
    - ``"missing_credentials"`` — operator opted in but ``anthropic``
      isn't importable or ``ANTHROPIC_API_KEY`` is unset; codex
      emits a structured warning and signal fields stay empty
    """

    status: AiStatus
    budget: AiBudget = field(default_factory=AiBudget)
    gpu_url: str | None = None
    gpu_auth_header: str | None = None
    cost_spent_usd: float = 0.0

    @property
    def runnable(self) -> bool:
        return self.status == "enabled"


def _ai_enabled() -> bool:
    raw = (os.environ.get("CODEX_AI_ENABLED") or "").strip().lower()
    return raw in {"true", "1", "yes", "on"}


def _tenant_entitled(tenant: str | None) -> bool:
    """Per-tenant AI entitlement gate.

    Two env vars compose the decision:

    - ``CODEX_AI_TENANTS_ALLOWLIST`` (comma-separated) — when set, ONLY
      these tenants can run AI; everyone else falls back to
      ``tenant_excluded``. Useful for staging AI on a single pilot
      tenant before rollout.
    - ``CODEX_AI_TENANTS_DENYLIST`` (comma-separated) — when set, the
      named tenants are blocked. Allowlist wins when both are set.

    Unset (the default) means every tenant is entitled.
    """
    if not tenant:
        return True
    allowlist_raw = (os.environ.get("CODEX_AI_TENANTS_ALLOWLIST") or "").strip()
    if allowlist_raw:
        allowed = {t.strip() for t in allowlist_raw.split(",") if t.strip()}
        return tenant in allowed
    denylist_raw = (os.environ.get("CODEX_AI_TENANTS_DENYLIST") or "").strip()
    if denylist_raw:
        denied = {t.strip() for t in denylist_raw.split(",") if t.strip()}
        return tenant not in denied
    return True


def _claude_available() -> bool:
    """Both the SDK package and an API key must be present.

    Imported lazily; ``anthropic`` is in the ``[ai]`` extras-bag so a
    base install doesn't depend on it.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _gpu_config() -> tuple[str | None, str | None]:
    url = os.environ.get("CODEX_AI_GPU_URL")
    disabled = (os.environ.get("CODEX_AI_GPU_DISABLED") or "").strip().lower()
    if disabled in {"true", "1", "yes", "on"}:
        return None, None
    if not url:
        return None, None
    auth = os.environ.get("CODEX_AI_GPU_AUTH_HEADER")
    return url, auth


def build_context(*, caller_skipped: bool, tenant: str | None = None) -> AiContext:
    """Build an :class:`AiContext` from environment + caller intent.

    Caller-skipped is computed by the API layer (which has access to
    request headers) and passed in as a boolean so this module stays
    framework-agnostic. ``tenant`` is the normalised tenant slug from
    ``X-Codex-Tenant``; entitlement is checked against the
    ``CODEX_AI_TENANTS_ALLOWLIST`` / ``CODEX_AI_TENANTS_DENYLIST``
    env knobs (see :func:`_tenant_entitled`).
    """
    if not _ai_enabled():
        return AiContext(status="disabled")
    if caller_skipped:
        return AiContext(status="skipped")
    if not _tenant_entitled(tenant):
        return AiContext(status="tenant_excluded")
    if not _claude_available():
        return AiContext(status="missing_credentials")
    gpu_url, gpu_auth = _gpu_config()
    return AiContext(status="enabled", gpu_url=gpu_url, gpu_auth_header=gpu_auth)
