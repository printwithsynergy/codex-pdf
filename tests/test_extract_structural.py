from pathlib import Path

from codex_pdf.extract.document import extract_from_path


def _fixture(path: str) -> Path:
    workspace = Path(__file__).resolve().parents[2]
    return workspace / "lint-pdf" / "tests" / "fixtures" / "pdfx4" / path


def test_extract_structural_from_conforming_fixture() -> None:
    pdf = _fixture("conforming/minimal.pdf")
    assert pdf.exists()
    doc = extract_from_path(pdf)
    assert len(doc.pages) >= 1
    # Conforming fixture should carry at least one output intent.
    assert len(doc.output_intents) >= 1


def test_extract_no_output_intent_fixture() -> None:
    pdf = _fixture("violating/no_output_intent.pdf")
    assert pdf.exists()
    doc = extract_from_path(pdf)
    assert len(doc.pages) >= 1
    assert len(doc.output_intents) == 0
