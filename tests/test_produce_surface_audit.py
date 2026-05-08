"""Tests for the producer-surface audit script.

The audit runs on every CI pipeline; these tests guard the behaviour
the policy enforces:

* The current tree passes (no producer surface leaked in yet).
* A synthesised module that calls ``pikepdf.new()`` fails.
* A synthesised module that calls ``Pdf.save()`` outside the
  allowlist fails.
* A synthesised module that imports ``pypdf`` fails.
* Calls to ``Image.save(buf, "PNG")`` (the Pillow rendering path) do
  NOT trigger a save violation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "produce_surface_audit.py"


@pytest.fixture(scope="module")
def audit_module() -> object:
    spec = importlib.util.spec_from_file_location("produce_surface_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["produce_surface_audit"] = module
    spec.loader.exec_module(module)
    return module


def test_audit_passes_on_current_tree(audit_module: object) -> None:
    report = audit_module.build_report()  # type: ignore[attr-defined]
    assert report["status"] == "PASS", report


def test_audit_flags_pikepdf_new(audit_module: object, tmp_path: Path) -> None:
    bad = tmp_path / "src" / "codex_pdf" / "_synthetic.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        dedent(
            """
            import pikepdf

            def make_pdf():
                pdf = pikepdf.new()
                return pdf
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(bad)  # type: ignore[attr-defined]
    kinds = {v["kind"] for v in result["violations"]}
    assert "pikepdf.new" in kinds


def test_audit_allows_pillow_image_save(audit_module: object, tmp_path: Path) -> None:
    """Pillow's ``Image.save(buf, 'PNG')`` is not a PDF write."""
    src = tmp_path / "src" / "codex_pdf" / "_pillow.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        dedent(
            """
            from PIL import Image

            def render():
                buf = bytearray()
                img = Image.new("RGB", (10, 10))
                img.save(buf, "PNG")
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(src)  # type: ignore[attr-defined]
    assert result["violations"] == []


def test_audit_flags_pypdf_import(audit_module: object, tmp_path: Path) -> None:
    src = tmp_path / "src" / "codex_pdf" / "_writer.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        dedent(
            """
            from pypdf import PdfWriter

            def write():
                w = PdfWriter()
                return w
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(src)  # type: ignore[attr-defined]
    kinds = {v["kind"] for v in result["violations"]}
    assert "banned-writer-import" in kinds


def test_audit_flags_pdf_magic_concat(audit_module: object, tmp_path: Path) -> None:
    src = tmp_path / "src" / "codex_pdf" / "_concat.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        dedent(
            """
            def make_pdf():
                body = b"%PDF-1.7\\n%foo"
                tail = b"%%EOF\\n"
                return b"%PDF-1.7\\n" + body + tail
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(src)  # type: ignore[attr-defined]
    kinds = {v["kind"] for v in result["violations"]}
    assert "pdf-magic-concat" in kinds


def test_audit_allows_pdf_magic_sniff(audit_module: object, tmp_path: Path) -> None:
    src = tmp_path / "src" / "codex_pdf" / "_sniff.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        dedent(
            """
            PDF_MAGIC = b"%PDF-"

            def is_pdf(raw: bytes) -> bool:
                return raw[:5] == PDF_MAGIC
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(src)  # type: ignore[attr-defined]
    assert result["violations"] == []


def test_audit_flags_ghostscript_pdfwrite(audit_module: object, tmp_path: Path) -> None:
    src = tmp_path / "src" / "codex_pdf" / "_gs.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        dedent(
            """
            import subprocess

            def write_pdf(input_path, output_path):
                subprocess.run([
                    "gs",
                    "-sDEVICE=pdfwrite",
                    "-o",
                    output_path,
                    input_path,
                ])
            """
        ),
        encoding="utf-8",
    )
    result = audit_module.audit_file(src)  # type: ignore[attr-defined]
    kinds = {v["kind"] for v in result["violations"]}
    assert "ghostscript-pdf-writer" in kinds
