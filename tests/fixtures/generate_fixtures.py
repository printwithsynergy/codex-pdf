"""Generate synthetic PDF fixtures for the PDF/X-4 smoke harness.

We can't ship the Ghent Workgroup corpus directly (license), and the engine's
PDF/X-4 validator works against the parsed SemanticDocument so a small set of
synthetic pikepdf-built PDFs is sufficient to exercise the binary path
end-to-end. For each fixture the harness expects a known set of finding IDs
to fire (or not).

Usage::

    python3 tests/fixtures/pdfx4/generate_fixtures.py

This script is idempotent — re-running it overwrites the existing PDFs with
deterministic content.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

_HERE = Path(__file__).resolve().parent
_CONFORMING = _HERE / "conforming"
_VIOLATING = _HERE / "violating"

_VALID_XMP = b"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about=""
    xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/"
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    pdfxid:GTS_PDFXVersion="PDF/X-4"
    pdfxid:GTS_PDFXConformance="PDF/X-4"
    pdf:PDFVersion="1.7"
    pdf:Trapped="False"
    xmp:CreateDate="2024-01-01T00:00:00Z"
    xmp:ModifyDate="2024-01-01T00:00:00Z">
    <dc:title><rdf:Alt><rdf:li xml:lang="x-default">PDFX4 Fixture</rdf:li></rdf:Alt></dc:title>
</rdf:Description>
</rdf:RDF>
</x:xmpmeta>"""


def _new_pdf() -> pikepdf.Pdf:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.TrimBox = pikepdf.Array([0, 0, 612, 792])
    pdf.docinfo["/Title"] = "PDFX4 Fixture"
    return pdf


def _save(pdf: pikepdf.Pdf, out: Path, *, force_version: str = "1.7") -> None:
    pdf.save(out, linearize=False, force_version=force_version)


def _write_xmp(pdf: pikepdf.Pdf, xmp: bytes) -> None:
    meta = pdf.make_stream(xmp)
    meta["/Type"] = pikepdf.Name("/Metadata")
    meta["/Subtype"] = pikepdf.Name("/XML")
    pdf.Root["/Metadata"] = meta


def _output_intent(pdf: pikepdf.Pdf) -> None:
    intent = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/OutputIntent"),
                "/S": pikepdf.Name("/GTS_PDFX"),
                "/OutputConditionIdentifier": "FOGRA39",
                "/RegistryName": "http://www.color.org",
                "/Info": "FOGRA39 coated",
            }
        )
    )
    pdf.Root["/OutputIntents"] = pikepdf.Array([intent])


def conforming_minimal() -> None:
    pdf = _new_pdf()
    _output_intent(pdf)
    _write_xmp(pdf, _VALID_XMP)
    out = _CONFORMING / "minimal.pdf"
    _save(pdf, out)
    print(f"Wrote {out}")


def violating_no_output_intent() -> None:
    pdf = _new_pdf()
    _write_xmp(pdf, _VALID_XMP)
    out = _VIOLATING / "no_output_intent.pdf"
    _save(pdf, out)
    print(f"Wrote {out}")


def violating_no_xmp() -> None:
    pdf = _new_pdf()
    _output_intent(pdf)
    out = _VIOLATING / "no_xmp.pdf"
    _save(pdf, out)
    print(f"Wrote {out}")


def violating_old_version() -> None:
    pdf = _new_pdf()
    _output_intent(pdf)
    _write_xmp(pdf, _VALID_XMP)
    out = _VIOLATING / "pdf_1_4.pdf"
    pdf.save(out, linearize=False, force_version="1.4")
    print(f"Wrote {out}")


def violating_no_trim_box() -> None:
    pdf = _new_pdf()
    # Strip the TrimBox we added in _new_pdf
    page = pdf.pages[0]
    if "/TrimBox" in page:
        del page["/TrimBox"]
    _output_intent(pdf)
    _write_xmp(pdf, _VALID_XMP)
    out = _VIOLATING / "no_trim_box.pdf"
    _save(pdf, out)
    print(f"Wrote {out}")


def main() -> None:
    _CONFORMING.mkdir(exist_ok=True)
    _VIOLATING.mkdir(exist_ok=True)
    conforming_minimal()
    violating_no_output_intent()
    violating_no_xmp()
    violating_old_version()
    violating_no_trim_box()


if __name__ == "__main__":
    main()
