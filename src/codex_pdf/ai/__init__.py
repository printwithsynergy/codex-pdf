"""Codex AI Signal extractors (Phase 1, 1.11.0).

This package implements the AI signal contract frozen in 1.10.0.
Six extractors land here:

- :mod:`codex_pdf.ai.language` — page dominant-language detection (Claude)
- :mod:`codex_pdf.ai.logos` — brand / logo detection (Claude vision)
- :mod:`codex_pdf.ai.symbols` — regulatory / packaging symbol detection (Claude vision)
- :mod:`codex_pdf.ai.barcodes` — barcode decoding (pure-CPU via ``pyzbar`` + ``pylibdmtx``)
- :mod:`codex_pdf.ai.classification` — document-level classification (Claude)
- :mod:`codex_pdf.ai.spell` — unknown-word candidates (Claude)

Two operator switches govern when extractors run; both default off:

- ``CODEX_AI_ENABLED`` — operator gate. When unset / false the package
  is dormant.
- ``CODEX_AI_COST_CAP_USD_PER_REQUEST`` — per-request hard cap on
  projected Claude spend (default ``"0.10"``). Honoured by
  :class:`AiBudget`; an extractor that would exceed the cap aborts
  with :class:`AiBudgetExceededError` and emits the
  ``ai_budget_exceeded`` warning.

The caller can opt out per-request with the ``X-Codex-Skip-AI`` header
(see :mod:`codex_pdf.api.main`). The combined gate decision is encoded
in :class:`AiContext`.
"""

from __future__ import annotations

from codex_pdf.ai.budget import AiBudget, AiBudgetExceededError
from codex_pdf.ai.context import AiContext, AiStatus, build_context
from codex_pdf.ai.dispatcher import run_signal, run_signals_on_document
from codex_pdf.ai.versions import AI_MODEL_VERSIONS

__all__ = [
    "AI_MODEL_VERSIONS",
    "AiBudget",
    "AiBudgetExceededError",
    "AiContext",
    "AiStatus",
    "build_context",
    "run_signal",
    "run_signals_on_document",
]
