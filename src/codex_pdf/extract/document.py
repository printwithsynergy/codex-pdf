"""Document extraction entrypoints."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any, Callable

# Shared pool for all parallel extraction passes (PyMuPDF + pikepdf). Created
# once per process (safe with gunicorn pre-fork: workers import this after
# fork). Sized for the 10-pass fan-out: structure / fonts / images /
# annotations / inventory / transparency (PyMuPDF) plus color / ocgs / forms /
# signals (pikepdf). cpu_count() on small Railway plans is 2; an 8-thread
# floor lets all passes run concurrently rather than queuing.
_EXTRACT_POOL = ThreadPoolExecutor(
    max_workers=max(os.cpu_count() or 4, 8),
    thread_name_prefix="codex-extract",
)

from codex_pdf.models.v1 import (
    CodexDocument,
    CodexInfoDict,
    CodexSourceRef,
    CodexXmpPacket,
)
from codex_pdf.version import __version__
from codex_pdf.extract.annotations import extract_annotations_fitz
from codex_pdf.extract.color import extract_color_world_pikepdf
from codex_pdf.extract.content_inventory import extract_page_inventory_fitz
from codex_pdf.extract.fonts import extract_fonts_fitz
from codex_pdf.extract.forms import extract_forms_pikepdf
from codex_pdf.extract.images import extract_images_fitz
from codex_pdf.extract.ocg import extract_ocgs_pikepdf
from codex_pdf.extract.signals import extract_analysis_signals_pikepdf
from codex_pdf.extract.structure import (
    conformance_claims_from_metadata,
    extract_structure_fitz,
)
from codex_pdf.extract.summary import build_document_summary
from codex_pdf.extract.trapping import derive_trapped_flag, extract_trap_evidence
from codex_pdf.extract.transparency import extract_transparency_fitz


# ---------------------------------------------------------------------------
# Parallel PyMuPDF pipeline.
#
# PyMuPDF ``Document`` objects are not safe to share across threads, so each
# pass opens its own handle from the same byte buffer (~1 ms per open). The
# stream-backed handle parses the xref table once; subsequent extractor calls
# reuse that parsed state.
# ---------------------------------------------------------------------------


def _fitz_open(raw: bytes):
    import fitz

    return fitz.open(stream=raw, filetype="pdf")


def _fitz_structure_pass(raw: bytes):
    """Structure + linearization + trap flag from a single doc handle."""
    doc = _fitz_open(raw)
    pdf_version, is_encrypted, info, xmp, pages = extract_structure_fitz(doc)
    is_linearized = bool(getattr(doc, "is_fast_webview", False))
    trapped_flag = derive_trapped_flag(doc)
    return pdf_version, is_encrypted, info, xmp, pages, is_linearized, trapped_flag


def _fitz_with_doc(raw: bytes, fn: Callable[[Any], Any]) -> Any:
    """Open a fresh fitz handle and pass it to ``fn``."""
    return fn(_fitz_open(raw))


def _run_fitz_pipeline(raw: bytes) -> dict[str, Any]:
    """Submit every PyMuPDF pass to the shared pool and merge results.

    Returns a dict with all PyMuPDF-derived fields. Inventory and
    transparency are attached to the structure-derived ``pages`` list
    before return so callers don't need to know the merge order.
    Failures in any single pass degrade to that field's empty default
    rather than aborting the whole pipeline.
    """
    pool = _EXTRACT_POOL
    f_structure = pool.submit(_fitz_structure_pass, raw)
    f_fonts = pool.submit(_fitz_with_doc, raw, extract_fonts_fitz)
    f_images = pool.submit(_fitz_with_doc, raw, extract_images_fitz)
    f_annotations = pool.submit(_fitz_with_doc, raw, extract_annotations_fitz)
    f_inventory = pool.submit(_fitz_with_doc, raw, extract_page_inventory_fitz)
    f_transparency = pool.submit(_fitz_with_doc, raw, extract_transparency_fitz)

    try:
        (
            pdf_version,
            is_encrypted,
            info,
            xmp,
            pages,
            is_linearized,
            trapped_flag,
        ) = f_structure.result()
    except Exception:
        pdf_version = "unknown"
        is_encrypted = False
        info = CodexInfoDict()
        xmp = CodexXmpPacket(present=False)
        pages = []
        is_linearized = False
        trapped_flag = None

    try:
        fonts = f_fonts.result()
    except Exception:
        fonts = []
    try:
        images = f_images.result()
    except Exception:
        images = []
    try:
        annotations = f_annotations.result()
    except Exception:
        annotations = []
    try:
        page_inventory = f_inventory.result()
    except Exception:
        page_inventory = {}
    try:
        transparency = f_transparency.result()
    except Exception:
        transparency = []

    for page in pages:
        page.inventory = page_inventory.get(page.page_num, [])
        if 0 < page.page_num <= len(transparency):
            page.transparency_tree = transparency[page.page_num - 1]

    return {
        "pdf_version": pdf_version,
        "is_encrypted": is_encrypted,
        "info": info,
        "xmp": xmp,
        "pages": pages,
        "is_linearized": is_linearized,
        "trapped_flag": trapped_flag,
        "fonts": fonts,
        "images": images,
        "annotations": annotations,
    }


def assemble_codex_document(
    pdf_bytes: bytes,
    *,
    source_uri: str | None,
    fitz_data: dict[str, Any],
    output_intents: list,
    color_spaces: list,
    ocgs: list,
    form_xobjects: list,
    analysis: dict[str, Any],
) -> CodexDocument:
    """Build a CodexDocument from pre-computed PyMuPDF + pikepdf results.

    Used by both the synchronous ``extract_document`` and the granular
    SSE generator in the API layer, which streams each pikepdf future
    individually and reuses this helper to materialise the final doc.
    """
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    trap_evidence = extract_trap_evidence(
        trapped_flag=fitz_data["trapped_flag"],
        ocg_names=[x.name for x in ocgs],
        annotation_subtypes=[x.subtype or "" for x in fitz_data["annotations"]],
    )
    doc = CodexDocument(
        codex_version=__version__,
        document_id=digest,
        source=CodexSourceRef(uri=source_uri, sha256=digest, size_bytes=len(pdf_bytes)),
        pdf_version=fitz_data["pdf_version"],
        is_encrypted=fitz_data["is_encrypted"],
        is_linearized=fitz_data["is_linearized"],
        conformance=conformance_claims_from_metadata(fitz_data["info"], fitz_data["xmp"]),
        info=fitz_data["info"],
        xmp=fitz_data["xmp"],
        trapped_flag=fitz_data["trapped_flag"],
        output_intents=output_intents,
        color_spaces=color_spaces,
        fonts=fitz_data["fonts"],
        images=fitz_data["images"],
        ocgs=ocgs,
        form_xobjects=form_xobjects,
        analysis=analysis,
        trap_evidence=trap_evidence,
        annotations=fitz_data["annotations"],
        pages=fitz_data["pages"],
    )
    doc.summary = build_document_summary(doc)
    # Detected text regions are populated for every page on the full
    # extract path. Failures are swallowed per page so a tricky PDF
    # never aborts the whole extract.
    try:
        from codex_pdf.extract.text_regions import populate_detected_text_regions

        populate_detected_text_regions(pdf_bytes, doc.pages)
    except Exception:
        pass
    return doc


def extract_document(pdf_bytes: bytes, *, source_uri: str | None = None) -> CodexDocument:
    """Extract a baseline CodexDocument from raw PDF bytes."""
    fitz_data = _run_fitz_pipeline(pdf_bytes)

    # Run the four independent pikepdf extractors concurrently.
    _f_color: Future = _EXTRACT_POOL.submit(extract_color_world_pikepdf, pdf_bytes)
    _f_ocgs: Future = _EXTRACT_POOL.submit(extract_ocgs_pikepdf, pdf_bytes)
    _f_forms: Future = _EXTRACT_POOL.submit(extract_forms_pikepdf, pdf_bytes)
    _f_signals: Future = _EXTRACT_POOL.submit(extract_analysis_signals_pikepdf, pdf_bytes)
    try:
        output_intents, color_spaces = _f_color.result()
    except Exception:
        output_intents, color_spaces = [], []
    try:
        ocgs = _f_ocgs.result()
    except Exception:
        ocgs = []
    try:
        form_xobjects = _f_forms.result()
    except Exception:
        form_xobjects = []
    try:
        analysis = _f_signals.result()
    except Exception:
        analysis = {}

    return assemble_codex_document(
        pdf_bytes,
        source_uri=source_uri,
        fitz_data=fitz_data,
        output_intents=output_intents,
        color_spaces=color_spaces,
        ocgs=ocgs,
        form_xobjects=form_xobjects,
        analysis=analysis,
    )


def extract_document_sparse(
    pdf_bytes: bytes,
    *,
    fields: set[str],
    source_uri: str | None = None,
) -> CodexDocument:
    """Run only the extractors required to populate *fields*.

    Used by the sparse-projection path when the caller sets
    ``X-Codex-Fields``.  The fitz pipeline always runs (it is fast and
    provides the core document structure); pikepdf passes are skipped
    when no requested field depends on them.  AI signals are not run
    here — the caller decides which AI kinds to request and runs them
    separately via the dispatcher.
    """
    from codex_pdf.extract.sparse import (
        resolve_groups,
        GRP_PIKEPDF_COLOR,
        GRP_PIKEPDF_OCGS,
        GRP_PIKEPDF_FORMS,
        GRP_PIKEPDF_SIGNALS,
    )

    groups = resolve_groups(fields)

    fitz_data = _run_fitz_pipeline(pdf_bytes)

    futures: dict[str, Future] = {}
    if GRP_PIKEPDF_COLOR in groups:
        futures["color"] = _EXTRACT_POOL.submit(extract_color_world_pikepdf, pdf_bytes)
    if GRP_PIKEPDF_OCGS in groups:
        futures["ocgs"] = _EXTRACT_POOL.submit(extract_ocgs_pikepdf, pdf_bytes)
    if GRP_PIKEPDF_FORMS in groups:
        futures["forms"] = _EXTRACT_POOL.submit(extract_forms_pikepdf, pdf_bytes)
    if GRP_PIKEPDF_SIGNALS in groups:
        futures["signals"] = _EXTRACT_POOL.submit(extract_analysis_signals_pikepdf, pdf_bytes)

    output_intents: list = []
    color_spaces: list = []
    ocgs: list = []
    form_xobjects: list = []
    analysis: dict = {}

    if "color" in futures:
        try:
            output_intents, color_spaces = futures["color"].result()
        except Exception:
            pass
    if "ocgs" in futures:
        try:
            ocgs = futures["ocgs"].result()
        except Exception:
            pass
    if "forms" in futures:
        try:
            form_xobjects = futures["forms"].result()
        except Exception:
            pass
    if "signals" in futures:
        try:
            analysis = futures["signals"].result()
        except Exception:
            pass

    return assemble_codex_document(
        pdf_bytes,
        source_uri=source_uri,
        fitz_data=fitz_data,
        output_intents=output_intents,
        color_spaces=color_spaces,
        ocgs=ocgs,
        form_xobjects=form_xobjects,
        analysis=analysis,
    )


def extract_document_fast(pdf_bytes: bytes, *, source_uri: str | None = None) -> CodexDocument:
    """Fast extract for Phase 1 streaming: PyMuPDF structure + parallel pikepdf colors/layers.

    Includes: pages (with real dimensions), fonts, images, annotations,
    output_intents, color_spaces, ocgs, and form_xobjects.

    Excludes: analysis signals (content_ops). Those are expensive and only
    needed for preflight lint checks — call extract_document() for the full
    result (Phase 2). This phase runs in ~400-700ms vs ~5-8s for full extract.
    """
    digest = hashlib.sha256(pdf_bytes).hexdigest()

    # Kick off the slow pikepdf passes first so they overlap with PyMuPDF.
    _f_color2: Future = _EXTRACT_POOL.submit(extract_color_world_pikepdf, pdf_bytes)
    _f_ocgs2: Future = _EXTRACT_POOL.submit(extract_ocgs_pikepdf, pdf_bytes)
    _f_forms2: Future = _EXTRACT_POOL.submit(extract_forms_pikepdf, pdf_bytes)

    fitz_data = _run_fitz_pipeline(pdf_bytes)

    output_intents: list = []
    color_spaces: list = []
    ocgs: list = []
    form_xobjects: list = []
    try:
        output_intents, color_spaces = _f_color2.result()
    except Exception:
        pass
    try:
        ocgs = _f_ocgs2.result()
    except Exception:
        pass
    try:
        form_xobjects = _f_forms2.result()
    except Exception:
        pass

    trap_evidence = extract_trap_evidence(
        trapped_flag=fitz_data["trapped_flag"],
        ocg_names=[x.name for x in ocgs],
        annotation_subtypes=[x.subtype or "" for x in fitz_data["annotations"]],
    )

    result = CodexDocument(
        codex_version=__version__,
        document_id=digest,
        source=CodexSourceRef(uri=source_uri, sha256=digest, size_bytes=len(pdf_bytes)),
        pdf_version=fitz_data["pdf_version"],
        is_encrypted=fitz_data["is_encrypted"],
        is_linearized=fitz_data["is_linearized"],
        conformance=conformance_claims_from_metadata(fitz_data["info"], fitz_data["xmp"]),
        info=fitz_data["info"],
        xmp=fitz_data["xmp"],
        trapped_flag=fitz_data["trapped_flag"],
        output_intents=output_intents,
        color_spaces=color_spaces,
        fonts=fitz_data["fonts"],
        images=fitz_data["images"],
        ocgs=ocgs,
        form_xobjects=form_xobjects,
        analysis={},
        trap_evidence=trap_evidence,
        annotations=fitz_data["annotations"],
        pages=fitz_data["pages"],
    )
    result.summary = build_document_summary(result)
    return result


def extract_document_pymupdf_only(
    pdf_bytes: bytes, *, source_uri: str | None = None
) -> CodexDocument:
    """PyMuPDF-only Phase 1 — no color world, no OCGs, no forms, no analysis.

    The lightest extract that still produces a valid CodexDocument. Used
    by the granular SSE path so the client gets structure + fonts +
    images + annotations within ~80-150 ms before the four pikepdf
    passes stream as separate granular events.
    """
    fitz_data = _run_fitz_pipeline(pdf_bytes)
    return assemble_codex_document(
        pdf_bytes,
        source_uri=source_uri,
        fitz_data=fitz_data,
        output_intents=[],
        color_spaces=[],
        ocgs=[],
        form_xobjects=[],
        analysis={},
    )


def extract_from_path(path: Path) -> CodexDocument:
    data = path.read_bytes()
    return extract_document(data, source_uri=str(path))
