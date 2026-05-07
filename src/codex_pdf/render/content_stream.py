"""Codex content-stream walker.

Wraps the existing analyzer-signal extractor in
:mod:`codex_pdf.extract.signals` so callers can request the codex
analysis side-channel for one page (or the whole document) via the
HTTP API. The body is the same dict the codex extractor publishes
under ``CodexDocument.analysis``.
"""

from __future__ import annotations

from typing import Any

from codex_pdf.extract.signals import extract_analysis_signals_pikepdf


def walk_content_stream(pdf_bytes: bytes, *, page_num: int = 1) -> dict[str, Any]:
    """Return codex analysis signals for ``pdf_bytes``.

    The signals match the structure already published as
    ``CodexDocument.analysis`` (spot_names, layer_names, page_1 with
    cs_to_spot, prop_to_ocg_name, content_ops, …). ``page_num`` is
    accepted for forward compatibility — today's pikepdf signal
    extractor always emits page-1 facts because that is what the
    consuming analyzers (dieline, dieline_quality, spot_name_normaliser)
    rely on.
    """
    signals = extract_analysis_signals_pikepdf(pdf_bytes)
    return {
        "page_num": page_num,
        "signals": signals,
    }


__all__ = ["walk_content_stream"]
