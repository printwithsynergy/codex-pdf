"""AI signal dispatcher — single entry point for the API layer.

Two top-level functions:

- :func:`run_signal` — run one signal kind for a single page (or the
  whole document, for ``"classification"``). Hits the cache first,
  calls the kind-specific extractor on miss, writes back. Returns a
  JSON-serialisable payload.
- :func:`run_signals_on_document` — run ALL signal kinds across an
  entire document during ``/v1/extract``. Mutates the codex
  document payload in place. Honours the cost cap; emits warnings
  on partial completion.

Both functions are synchronous + safe to run inside
``loop.run_in_executor`` from the FastAPI request handler.

This module owns the "should we render?" decision — rendering a page
to PNG is expensive enough to skip when no vision-based extractor is
going to consume it.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from codex_pdf.ai import (
    barcodes as barcodes_mod,
    classification as classification_mod,
    language as language_mod,
    logos as logos_mod,
    spell as spell_mod,
    symbols as symbols_mod,
    trap_zones as trap_zones_mod,
)
from codex_pdf.ai.budget import AiBudgetExceededError
from codex_pdf.ai.cache import get_cached, set_cached, signal_cache_key
from codex_pdf.ai.context import AiContext

logger = logging.getLogger(__name__)

DEFAULT_RENDER_DPI = 150

_PAGE_KINDS = frozenset({"language", "logos", "symbols", "barcodes", "spell", "trap_zones"})
_DOCUMENT_KINDS = frozenset({"classification"})


class SignalResult:
    """Result envelope returned by :func:`run_signal`."""

    __slots__ = ("kind", "data", "from_cache", "warning")

    def __init__(
        self,
        *,
        kind: str,
        data: Any,
        from_cache: bool = False,
        warning: dict[str, str] | None = None,
    ) -> None:
        self.kind = kind
        self.data = data
        self.from_cache = from_cache
        self.warning = warning


def _render_page_png(pdf_bytes: bytes, page_index: int, dpi: int) -> bytes:
    """Rasterise one page to PNG for vision extractors.

    Returns empty bytes on failure — vision extractors then degrade
    to empty results (with a logged warning) rather than crashing.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF (fitz) not available for AI signal rasterisation")
        return b""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_index >= doc.page_count or page_index < 0:
                return b""
            page = doc.load_page(page_index)
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            return pix.tobytes("png")
    except Exception:
        logger.exception("AI signal rasterisation failed (page=%s)", page_index)
        return b""


def _page_size_pt(pdf_bytes: bytes, page_index: int) -> tuple[float, float]:
    """Return ``(width_pt, height_pt)`` for one page, or ``(0,0)`` on error."""
    try:
        import fitz
    except ImportError:
        return (0.0, 0.0)
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_index >= doc.page_count or page_index < 0:
                return (0.0, 0.0)
            rect = doc.load_page(page_index).rect
            return (rect.width, rect.height)
    except Exception:
        logger.exception("AI signal page size lookup failed (page=%s)", page_index)
        return (0.0, 0.0)


def _document_text(payload: dict[str, Any]) -> str:
    """Concatenate every page's plain text into one string for the
    document-scoped extractors."""
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return ""
    chunks: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        # Codex stores per-page text on either ``text`` or
        # ``analysis.text`` depending on extract version. Coalesce.
        text = page.get("text")
        if not isinstance(text, str):
            analysis = page.get("analysis")
            if isinstance(analysis, dict):
                text = analysis.get("text") if isinstance(analysis.get("text"), str) else ""
        if isinstance(text, str) and text.strip():
            chunks.append(text)
    return "\n\n".join(chunks)


def _page_text(payload: dict[str, Any], page_index: int) -> str:
    pages = payload.get("pages")
    if not isinstance(pages, list) or page_index >= len(pages):
        return ""
    page = pages[page_index]
    if not isinstance(page, dict):
        return ""
    text = page.get("text")
    if isinstance(text, str):
        return text
    analysis = page.get("analysis")
    if isinstance(analysis, dict):
        text = analysis.get("text")
        if isinstance(text, str):
            return text
    return ""


def run_signal(
    *,
    context: AiContext,
    cache: Any,
    pdf_bytes: bytes,
    payload: dict[str, Any],
    tenant: str,
    pdf_hash: str,
    kind: str,
    page_index: int | None = None,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> SignalResult:
    """Resolve one signal kind, hitting the cache when possible.

    Page-scoped kinds require ``page_index``; document-scoped kinds
    require ``page_index is None``. Returns a :class:`SignalResult`
    with the kind-specific payload shape.
    """
    if kind in _PAGE_KINDS and page_index is None:
        raise ValueError(f"signal kind {kind!r} is page-scoped; page_index required")
    if kind in _DOCUMENT_KINDS and page_index is not None:
        raise ValueError(f"signal kind {kind!r} is document-scoped; page_index forbidden")

    key = signal_cache_key(
        tenant=tenant,
        pdf_hash=pdf_hash,
        kind=kind,
        page_index=page_index,
    )

    cached = get_cached(cache, key)
    if cached is not None:
        return SignalResult(kind=kind, data=cached, from_cache=True)

    if not context.runnable:
        return SignalResult(kind=kind, data=_empty_for_kind(kind))

    try:
        data = _dispatch(
            context=context,
            pdf_bytes=pdf_bytes,
            payload=payload,
            kind=kind,
            page_index=page_index,
            render_dpi=render_dpi,
        )
    except AiBudgetExceededError as exc:
        return SignalResult(
            kind=kind,
            data=_empty_for_kind(kind),
            warning={
                "code": "ai_budget_exceeded",
                "message": str(exc),
                "scope": f"signals.{kind}",
            },
        )

    set_cached(cache, key, data)
    return SignalResult(kind=kind, data=data)


def _dispatch(
    *,
    context: AiContext,
    pdf_bytes: bytes,
    payload: dict[str, Any],
    kind: str,
    page_index: int | None,
    render_dpi: int,
) -> Any:
    if kind == "language":
        text = _page_text(payload, page_index or 0)
        result = language_mod.extract_language(context=context, page_text=text)
        return result.model_dump(mode="json") if result is not None else None
    if kind == "spell":
        text = _page_text(payload, page_index or 0)
        return spell_mod.extract_spell(context=context, page_text=text)
    if kind == "barcodes":
        width_pt, height_pt = _page_size_pt(pdf_bytes, page_index or 0)
        png = _render_page_png(pdf_bytes, page_index or 0, render_dpi)
        results = barcodes_mod.extract_barcodes(
            context=context,
            page_png=png,
            page_height_pt=height_pt,
            render_dpi=render_dpi,
        )
        return [r.model_dump(mode="json") for r in results]
    if kind == "logos":
        width_pt, height_pt = _page_size_pt(pdf_bytes, page_index or 0)
        png = _render_page_png(pdf_bytes, page_index or 0, render_dpi)
        results = logos_mod.extract_logos(
            context=context,
            page_png=png,
            page_width_pt=width_pt,
            page_height_pt=height_pt,
        )
        return [r.model_dump(mode="json") for r in results]
    if kind == "symbols":
        width_pt, height_pt = _page_size_pt(pdf_bytes, page_index or 0)
        png = _render_page_png(pdf_bytes, page_index or 0, render_dpi)
        results = symbols_mod.extract_symbols(
            context=context,
            page_png=png,
            page_width_pt=width_pt,
            page_height_pt=height_pt,
        )
        return [r.model_dump(mode="json") for r in results]
    if kind == "trap_zones":
        width_pt, height_pt = _page_size_pt(pdf_bytes, page_index or 0)
        png = _render_page_png(pdf_bytes, page_index or 0, render_dpi)
        results = trap_zones_mod.extract_trap_zones(
            context=context,
            page_png=png,
            page_index=page_index or 0,
            page_width_pt=width_pt,
            page_height_pt=height_pt,
        )
        return [r.model_dump(mode="json") for r in results]
    if kind == "classification":
        text = _document_text(payload)
        return classification_mod.extract_classification(
            context=context, document_text=text
        )
    raise ValueError(f"unknown signal kind: {kind!r}")


def _empty_for_kind(kind: str) -> Any:
    if kind == "language":
        return None
    if kind in {"logos", "symbols", "barcodes", "spell", "trap_zones"}:
        return []
    if kind == "classification":
        return {}
    return None


def run_signals_on_document(
    *,
    context: AiContext,
    cache: Any,
    pdf_bytes: bytes,
    payload: dict[str, Any],
    tenant: str,
    pdf_hash: str,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> list[dict[str, str]]:
    """Run every signal kind across the document, mutating ``payload``.

    Returns a list of warnings to be appended to
    ``extraction_warnings`` — typically empty when everything ran,
    or ``ai_budget_exceeded`` entries when the cost cap stopped
    midway.
    """
    if not context.runnable:
        return []

    warnings: list[dict[str, str]] = []
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return warnings

    budget_stopped = False

    # Document classification first — fastest signal, lets the consumer
    # see something even if the per-page lane gets capped.
    cls_result = run_signal(
        context=context,
        cache=cache,
        pdf_bytes=pdf_bytes,
        payload=payload,
        tenant=tenant,
        pdf_hash=pdf_hash,
        kind="classification",
    )
    if cls_result.warning is not None:
        warnings.append(cls_result.warning)
        budget_stopped = True
    payload["document_classification"] = cls_result.data or {}

    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        if budget_stopped:
            break
        for kind in ("language", "barcodes", "symbols", "logos", "spell", "trap_zones"):
            result = run_signal(
                context=context,
                cache=cache,
                pdf_bytes=pdf_bytes,
                payload=payload,
                tenant=tenant,
                pdf_hash=pdf_hash,
                kind=kind,
                page_index=idx,
                render_dpi=render_dpi,
            )
            if result.warning is not None:
                warnings.append(result.warning)
                budget_stopped = True
                break
            _attach_to_page(page, kind, result.data)
    return warnings


def _attach_to_page(page: dict[str, Any], kind: str, data: Any) -> None:
    if kind == "language":
        page["detected_language"] = data
    elif kind == "logos":
        page["detected_logos"] = data or []
    elif kind == "symbols":
        page["detected_symbols"] = data or []
    elif kind == "barcodes":
        page["detected_barcodes"] = data or []
    elif kind == "spell":
        page["spell_candidates"] = data or []
    elif kind == "trap_zones":
        page["trap_zone_candidates"] = data or []
