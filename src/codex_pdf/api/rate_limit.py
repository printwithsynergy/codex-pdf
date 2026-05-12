"""In-process rate limiter for compute-and-cache POSTs.

Token-bucket per ``(tenant, endpoint)`` pair. Tenants get an
independent quota so a noisy neighbour can't starve another. Bucket
sizes are environment-configurable so the operator can dial them
without a redeploy:

- ``CODEX_RATE_LIMIT_RPM`` — refills per minute (default 120).
- ``CODEX_RATE_LIMIT_BURST`` — max burst size (default 30).

Set ``CODEX_RATE_LIMIT_DISABLED=true`` to bypass entirely (single
tenant deployments, dev loops).

The limiter is intentionally in-process and per-replica. In a
multi-replica deployment with N replicas, the effective limit is
``N * rpm``. That's fine for the rc.1 → 1.9.0 window — the
operational contract is "we have a quota and a Retry-After"; the
exact distributed accounting is a Phase 4 follow-up.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class RateDecision:
    """Outcome of a rate-limit consult."""

    allowed: bool
    retry_after_seconds: float = 0.0


class RateLimiter:
    """Token bucket per ``(tenant, endpoint)``.

    ``rpm`` tokens per minute refill rate; ``burst`` cap on the
    bucket. A miss returns the time until the next token would be
    available so callers can set a ``Retry-After`` header.
    """

    def __init__(self, *, rpm: int, burst: int) -> None:
        self._rpm = max(1, rpm)
        self._burst = max(1, burst)
        self._refill_per_second = self._rpm / 60.0
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, tenant: str, endpoint: str) -> RateDecision:
        now = time.monotonic()
        key = (tenant, endpoint)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self._burst), last_refill=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(
                float(self._burst), bucket.tokens + elapsed * self._refill_per_second
            )
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return RateDecision(allowed=True)
            # Bucket empty: compute time to next whole token.
            deficit = 1.0 - bucket.tokens
            retry_after = deficit / self._refill_per_second if self._refill_per_second else 60.0
            return RateDecision(allowed=False, retry_after_seconds=retry_after)


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"true", "1", "yes", "on"}


def make_rate_limiter() -> RateLimiter | None:
    """Build the process-wide limiter from env, or return ``None``.

    Returns ``None`` when ``CODEX_RATE_LIMIT_DISABLED=true`` so
    callers can short-circuit. The codex API treats the limiter as a
    soft dependency: misconfigured env values fall back to the
    documented defaults rather than failing service boot.
    """
    if _truthy(os.environ.get("CODEX_RATE_LIMIT_DISABLED")):
        return None
    try:
        rpm = int(os.environ.get("CODEX_RATE_LIMIT_RPM") or 120)
    except ValueError:
        rpm = 120
    try:
        burst = int(os.environ.get("CODEX_RATE_LIMIT_BURST") or 30)
    except ValueError:
        burst = 30
    return RateLimiter(rpm=rpm, burst=burst)
