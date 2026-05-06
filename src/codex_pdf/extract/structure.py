"""Document/page structural extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.extract.common import safe_box
from codex_pdf.models.v1 import (
    CodexConformanceClaims,
    CodexInfoDict,
    CodexPage,
    CodexPageBoxes,
    CodexTrapEvidence,
    CodexXmpPacket,
)


def extract_structure_fitz(doc: Any) -> tuple[str, bool, CodexInfoDict, CodexXmpPacket, list[CodexPage]]:
    meta = doc.metadata or {}
    fmt = str(meta.get("format") or "").strip()
    pdf_version = fmt.replace("PDF", "").strip() if fmt else "unknown"
    is_encrypted = bool(getattr(doc, "needs_pass", False))
    info = CodexInfoDict(
        title=meta.get("title") or None,
        author=meta.get("author") or None,
        subject=meta.get("subject") or None,
        creator=meta.get("creator") or None,
        producer=meta.get("producer") or None,
        creation_date=meta.get("creationDate") or None,
        mod_date=meta.get("modDate") or None,
    )
    try:
        xmp_raw = doc.xref_xml_metadata()
        xmp = CodexXmpPacket(present=bool(xmp_raw))
    except Exception:
        xmp = CodexXmpPacket(present=False)

    pages: list[CodexPage] = []
    for idx, page in enumerate(doc, start=1):
        media = safe_box(page.rect)
        boxes = CodexPageBoxes(media=media, crop=media, bleed=media, trim=media, art=media)
        pages.append(CodexPage(page_num=idx, rotation=int(page.rotation), boxes=boxes))

    return pdf_version, is_encrypted, info, xmp, pages


def conformance_claims_from_metadata(info: CodexInfoDict, xmp: CodexXmpPacket) -> CodexConformanceClaims:
    # Placeholder heuristics based on currently available metadata.
    pdfx = "unknown" if xmp.present else None
    return CodexConformanceClaims(pdfx=pdfx, pdfa=xmp.pdfa_part, pdfua=xmp.pdfua_part)


def trap_evidence_from_metadata(trapped_flag: str | None) -> CodexTrapEvidence:
    notes: list[str] = []
    if trapped_flag is not None:
        notes.append("Derived from document metadata /Trapped field.")
    return CodexTrapEvidence(trapped_flag=trapped_flag, interpretation_notes=notes)
