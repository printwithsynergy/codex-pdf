"""Document extraction entrypoints."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

from codex_pdf.models.v1 import CodexDocument, CodexInfoDict, CodexSourceRef, CodexXmpPacket
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


def extract_document(pdf_bytes: bytes, *, source_uri: str | None = None) -> CodexDocument:
    """Extract a baseline CodexDocument from raw PDF bytes."""
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    pages = []
    fonts = []
    images = []
    annotations = []
    output_intents = []
    color_spaces = []
    ocgs = []
    form_xobjects = []
    info = CodexInfoDict()
    xmp = CodexXmpPacket(present=False)
    pdf_version = "unknown"
    is_encrypted = False
    trapped_flag = None
    analysis: dict[str, object] = {}

    is_linearized = False
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pdf_version, is_encrypted, info, xmp, pages = extract_structure_fitz(doc)
        fonts = extract_fonts_fitz(doc)
        images = extract_images_fitz(doc)
        annotations = extract_annotations_fitz(doc)

        page_inventory = extract_page_inventory_fitz(doc)
        transparency = extract_transparency_fitz(doc)
        for page in pages:
            page.inventory = page_inventory.get(page.page_num, [])
            if 0 < page.page_num <= len(transparency):
                page.transparency_tree = transparency[page.page_num - 1]

        trapped_flag = derive_trapped_flag(doc)
        # Linearized PDFs (fast web view) have a /Linearized dict in the first xref.
        is_linearized = bool(getattr(doc, "is_fast_webview", False))
    except Exception:
        # Fall back to skeleton with minimal metadata.
        pass

    # Run the four independent pikepdf extractors concurrently.
    with ThreadPoolExecutor(max_workers=4) as _pool:
        _f_color: Future = _pool.submit(extract_color_world_pikepdf, pdf_bytes)
        _f_ocgs: Future = _pool.submit(extract_ocgs_pikepdf, pdf_bytes)
        _f_forms: Future = _pool.submit(extract_forms_pikepdf, pdf_bytes)
        _f_signals: Future = _pool.submit(extract_analysis_signals_pikepdf, pdf_bytes)
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
    trap_evidence = extract_trap_evidence(
        trapped_flag=trapped_flag,
        ocg_names=[x.name for x in ocgs],
        annotation_subtypes=[x.subtype or "" for x in annotations],
    )

    doc = CodexDocument(
        codex_version=__version__,
        document_id=digest,
        source=CodexSourceRef(uri=source_uri, sha256=digest, size_bytes=len(pdf_bytes)),
        pdf_version=pdf_version,
        is_encrypted=is_encrypted,
        is_linearized=is_linearized,
        conformance=conformance_claims_from_metadata(info, xmp),
        info=info,
        xmp=xmp,
        trapped_flag=trapped_flag,
        output_intents=output_intents,
        color_spaces=color_spaces,
        fonts=fonts,
        images=images,
        ocgs=ocgs,
        form_xobjects=form_xobjects,
        analysis=analysis,
        trap_evidence=trap_evidence,
        annotations=annotations,
        pages=pages,
    )
    doc.summary = build_document_summary(doc)
    return doc


def extract_document_fast(pdf_bytes: bytes, *, source_uri: str | None = None) -> CodexDocument:
    """PyMuPDF-only extraction — returns page dimensions and structure in ~200ms.

    ocgs, color_spaces, output_intents, form_xobjects, and analysis are empty.
    Intended as Phase 1 in a two-phase streaming extract; call extract_document()
    for the full result with spot colors, signals, and layer data.
    """
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    pages = []
    fonts = []
    images = []
    annotations = []
    info = CodexInfoDict()
    xmp = CodexXmpPacket(present=False)
    pdf_version = "unknown"
    is_encrypted = False
    trapped_flag = None
    is_linearized = False

    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pdf_version, is_encrypted, info, xmp, pages = extract_structure_fitz(doc)
        fonts = extract_fonts_fitz(doc)
        images = extract_images_fitz(doc)
        annotations = extract_annotations_fitz(doc)
        page_inventory = extract_page_inventory_fitz(doc)
        transparency = extract_transparency_fitz(doc)
        for page in pages:
            page.inventory = page_inventory.get(page.page_num, [])
            if 0 < page.page_num <= len(transparency):
                page.transparency_tree = transparency[page.page_num - 1]
        trapped_flag = derive_trapped_flag(doc)
        is_linearized = bool(getattr(doc, "is_fast_webview", False))
    except Exception:
        pass

    trap_evidence = extract_trap_evidence(
        trapped_flag=trapped_flag,
        ocg_names=[],
        annotation_subtypes=[x.subtype or "" for x in annotations],
    )

    result = CodexDocument(
        codex_version=__version__,
        document_id=digest,
        source=CodexSourceRef(uri=source_uri, sha256=digest, size_bytes=len(pdf_bytes)),
        pdf_version=pdf_version,
        is_encrypted=is_encrypted,
        is_linearized=is_linearized,
        conformance=conformance_claims_from_metadata(info, xmp),
        info=info,
        xmp=xmp,
        trapped_flag=trapped_flag,
        output_intents=[],
        color_spaces=[],
        fonts=fonts,
        images=images,
        ocgs=[],
        form_xobjects=[],
        analysis={},
        trap_evidence=trap_evidence,
        annotations=annotations,
        pages=pages,
    )
    result.summary = build_document_summary(result)
    return result


def extract_from_path(path: Path) -> CodexDocument:
    data = path.read_bytes()
    return extract_document(data, source_uri=str(path))
