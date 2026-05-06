from pathlib import Path

from codex_pdf.extract.document import extract_from_path


def _pdf(path: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "lint-pdf" / "tests" / "fixtures" / "pdfx4" / path


def test_conforming_fixture_profile() -> None:
    doc = extract_from_path(_pdf("conforming/minimal.pdf"))
    assert doc.pdf_version == "1.7"
    assert len(doc.pages) == 1
    assert len(doc.output_intents) >= 1
    assert isinstance(doc.fonts, list)


def test_no_trim_fixture_profile() -> None:
    doc = extract_from_path(_pdf("violating/no_trim_box.pdf"))
    assert len(doc.pages) == 1
    # codex normalizes absent trim to media fallback in page boxes.
    assert doc.pages[0].boxes.trim is not None


def test_deterministic_document_id() -> None:
    fixture = _pdf("violating/no_output_intent.pdf")
    first = extract_from_path(fixture)
    second = extract_from_path(fixture)
    assert first.document_id == second.document_id
