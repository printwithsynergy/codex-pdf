"""Extraction interfaces."""

from codex_pdf.extract.conformance import (
    compute_conformance_verdict,
    known_profiles,
)
from codex_pdf.extract.document import (
    assemble_codex_document,
    extract_document,
    extract_document_fast,
    extract_document_pymupdf_only,
    extract_document_sparse,
    extract_from_path,
)
from codex_pdf.extract.probe import extract_probe_min, extract_probe_std
from codex_pdf.extract.text_regions import (
    extract_text_regions_for_page,
    populate_detected_text_regions,
)

__all__ = [
    "assemble_codex_document",
    "compute_conformance_verdict",
    "extract_document",
    "extract_document_fast",
    "extract_document_pymupdf_only",
    "extract_document_sparse",
    "extract_from_path",
    "extract_probe_min",
    "extract_probe_std",
    "extract_text_regions_for_page",
    "known_profiles",
    "populate_detected_text_regions",
]
