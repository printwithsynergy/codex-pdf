"""Per-request cost cap for AI signal extractors.

Codex's AI extractors call Anthropic (Claude). Each call has a
projected USD cost based on token counts and model pricing.
:class:`AiBudget` aggregates those projections across one
``/v1/extract`` request and refuses to admit the next call once the
running total would exceed the cap.

The cap is read from ``CODEX_AI_COST_CAP_USD_PER_REQUEST`` (default
``"0.10"``); any failure to parse the env var falls back to the
default so a misconfiguration can't disable the guard rail.

Cost numbers are reviewed against Anthropic's public pricing page
(per million tokens) and stored centrally so a model swap touches
one file. Numbers reviewed against Anthropic pricing as of 2026-Q1
— bump :data:`PRICING_REVIEWED_AT` when refreshing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Lock
from typing import Final

PRICING_REVIEWED_AT: Final[str] = "2026-01-15"

# USD per 1M tokens. Source: Anthropic public pricing page.
_PRICING: Final[dict[str, tuple[float, float]]] = {
    # input, output
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
}

# A vision API call charges per-image plus the regular input/output token
# rates. Approximation: a 1024x1024 image counts as ~1600 input tokens for
# Claude. We track image count separately so the projection stays explicit.
_VISION_TOKENS_PER_IMAGE: Final[int] = 1600


class AiBudgetExceededError(RuntimeError):
    """Raised when admitting the next call would exceed the cap."""

    def __init__(self, *, projected_usd: float, cap_usd: float, kind: str) -> None:
        super().__init__(
            f"AI cost cap exceeded: projected ${projected_usd:.4f} would "
            f"exceed cap ${cap_usd:.4f} (signal kind: {kind})"
        )
        self.projected_usd = projected_usd
        self.cap_usd = cap_usd
        self.kind = kind


def _read_cap() -> float:
    raw = os.environ.get("CODEX_AI_COST_CAP_USD_PER_REQUEST")
    if not raw:
        return 0.10
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.10
    if value <= 0:
        return 0.10
    return value


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    images: int = 0,
) -> float:
    """Project the USD cost of a single Anthropic call.

    Unknown models fall back to claude-sonnet-4-6 pricing so the
    estimate is conservative — better to over-project and refuse than
    silently sail past the cap.
    """
    rates = _PRICING.get(model) or _PRICING["claude-sonnet-4-6"]
    input_rate_per_token = rates[0] / 1_000_000
    output_rate_per_token = rates[1] / 1_000_000
    image_tokens = images * _VISION_TOKENS_PER_IMAGE
    return (
        (input_tokens + image_tokens) * input_rate_per_token
        + output_tokens * output_rate_per_token
    )


@dataclass
class AiBudget:
    """Aggregate cost projection for one ``/v1/extract`` request.

    Thread-safe under :attr:`_lock` — Phase 1 runs extractors
    sequentially, but per-page parallelism is on the Phase 4 roadmap
    and the lock costs effectively nothing.
    """

    cap_usd: float = field(default_factory=_read_cap)
    spent_usd: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def admit(
        self,
        *,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        images: int = 0,
    ) -> float:
        """Reserve budget for a planned call and return its cost.

        Raises :class:`AiBudgetExceededError` when the caller hasn't
        got room for this call. Callers should catch and surface as
        an ``ai_budget_exceeded`` warning; do NOT downgrade to a
        500.
        """
        cost = estimate_cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            images=images,
        )
        with self._lock:
            projected = self.spent_usd + cost
            if projected > self.cap_usd:
                raise AiBudgetExceededError(
                    projected_usd=projected,
                    cap_usd=self.cap_usd,
                    kind=kind,
                )
            self.spent_usd = projected
        return cost

    @property
    def remaining_usd(self) -> float:
        with self._lock:
            return max(0.0, self.cap_usd - self.spent_usd)
