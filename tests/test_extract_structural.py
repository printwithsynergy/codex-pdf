from pathlib import Path

import pytest

from codex_pdf.extract.document import extract_from_path


def _fixture(path: str) -> Path:
    """Resolve a lint-pdf fixture path, skipping the test if not present.

    Two layouts are supported:
      - Local dev: ``$HOME/lint-pdf`` adjacent to ``$HOME/codex-pdf``
        (``Path(__file__).parents[2] / "lint-pdf"``).
      - CI: ``lint-pdf`` checked out next to ``codex-pdf`` inside the
        workspace (``Path(__file__).parents[1].parent / "lint-pdf"``)
        OR inside the codex-pdf workspace (``parents[1] / "lint-pdf"``).
    """
    candidates = [
        Path(__file__).resolve().parents[2] / "lint-pdf" / "tests" / "fixtures" / "pdfx4" / path,
        Path(__file__).resolve().parents[1] / "lint-pdf" / "tests" / "fixtures" / "pdfx4" / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip(f"lint-pdf fixture not found: {path}")


def test_extract_structural_from_conforming_fixture() -> None:
    pdf = _fixture("conforming/minimal.pdf")
    doc = extract_from_path(pdf)
    assert len(doc.pages) >= 1
    # Conforming fixture should carry at least one output intent.
    assert len(doc.output_intents) >= 1


def test_extract_no_output_intent_fixture() -> None:
    pdf = _fixture("violating/no_output_intent.pdf")
    doc = extract_from_path(pdf)
    assert len(doc.pages) >= 1
    assert len(doc.output_intents) == 0
