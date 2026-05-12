"""Per-extractor model + prompt versioning.

Phase 4 of the AI Signal Campaign — frozen identifiers consumers
can pin against. Every AI signal payload carries the version
that produced it (via the per-extractor ``SOURCE`` constant), but
exposing the catalogue here lets ``GET /v1/contract`` advertise it
so SDK consumers can validate ahead of time.

Bump policy:

- ``model`` bumps when codex swaps a Claude model family (Haiku ↔
  Sonnet). Counts as a minor codex release.
- ``prompt`` bumps when the system prompt changes shape (new
  required fields, removed labels). Counts as a minor codex release.
- ``schema`` is the per-signal payload contract version — bumps
  only when the kind-specific JSON shape changes. Stays
  backward-compatible within a 1.x line.

Cache keys for AI signals are keyed by ``(tenant, pdf_hash,
page_index, kind)`` — they do NOT include the prompt or model
version. Operators who want to force a re-extraction after a
prompt bump should rotate the codex-pdf ``VERSION`` constant
(which is part of the cache namespace).
"""

from __future__ import annotations

from typing import Final

AI_MODEL_VERSIONS: Final[dict[str, dict[str, str]]] = {
    "language": {
        "model": "claude-haiku-4-5",
        "prompt": "lang-1",
        "schema": "1.0.0",
    },
    "logos": {
        "model": "claude-sonnet-4-6",
        "prompt": "logos-1",
        "schema": "1.0.0",
    },
    "symbols": {
        "model": "claude-sonnet-4-6",
        "prompt": "symbols-1",
        "schema": "1.0.0",
    },
    "barcodes": {
        "model": "pyzbar+pylibdmtx",
        "prompt": "n/a",  # pure CPU lane, no prompt
        "schema": "1.0.0",
    },
    "classification": {
        "model": "claude-haiku-4-5",
        "prompt": "class-1",
        "schema": "1.0.0",
    },
    "spell": {
        "model": "claude-haiku-4-5",
        "prompt": "spell-1",
        "schema": "1.0.0",
    },
}
