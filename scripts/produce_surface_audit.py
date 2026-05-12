"""Producer-surface audit for codex-pdf.

Codex is the canonical *read-only* PDF facts + render service. It may
freely import :mod:`pikepdf` and :mod:`pymupdf` to read PDFs and to
extract analysis facts; it may invoke Ghostscript with raster output
devices (``tiff24nc``, ``tiffsep``, ``png16m``) to rasterise pages and
separations. What it must NEVER do is produce new PDF bytes — the
moment a producer surface (``pdfwrite``, ``pikepdf.new``, a
``Pdf.save`` outside the explicit OCG-override transient, a
``PdfWriter`` import, a raw ``%PDF-`` literal being concatenated) leaks
into the codex tree, the read-only invariant has been broken and the
Forge / producer responsibilities have started bleeding into Codex.

This audit walks ``src/codex_pdf/`` with Python's :mod:`ast`,
classifies every call site, and fails when:

* ``pikepdf.new()`` is called.
* ``Pdf.save(...)`` is invoked anywhere outside the
  ``apply_ocg_overrides`` allowlisted helper (which writes a transient
  in-memory PDF only to feed Ghostscript with the requested OCG
  override applied).
* ``Pdf.save_bytes`` is called.
* :mod:`pypdf`, :mod:`pdfrw`, :mod:`reportlab`, or :mod:`fpdf` is
  imported (those packages exist *only* to write PDFs).
* A Ghostscript subprocess invocation passes ``-sDEVICE=pdfwrite`` or
  any ``-sDEVICE=pdfimage*`` string.
* ``mutool clean|create|merge`` or ``qpdf`` write modes or ``cpdf`` is
  invoked.
* ``b"%PDF-"`` (or its ``"%PDF-"`` string twin) is concatenated /
  written to disk. Read-only sniffs (``startswith(b"%PDF-")``,
  ``raw[:5] == b"%PDF-"``) are allowed.

The audit also enforces the parser-surface side of the invariant —
codex IS allowed to import pikepdf + pymupdf, but only inside
:mod:`codex_pdf.extract`, :mod:`codex_pdf.render`,
:mod:`codex_pdf.preflight_ingest`, :mod:`codex_pdf.eval`,
:mod:`codex_pdf.color` (read-only inkbook loading), and
:mod:`codex_pdf.geom` (no PDF I/O — pure geometry). Any other module
importing pikepdf / pymupdf is flagged so future contributors can't
sneak a write-path in via an unrelated module.

Exits non-zero on any violation. Emits a JSON report when ``--json``
is supplied so CI can pin the audit alongside the parity baseline.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "codex_pdf"


# --- Producer-surface ban list --------------------------------------------

BANNED_WRITER_MODULES: tuple[str, ...] = (
    "pypdf",
    "pdfrw",
    "reportlab",
    "fpdf",
    "fpdf2",
    "borb",
    "pdfkit",
)

BANNED_GHOSTSCRIPT_DEVICES: tuple[str, ...] = (
    "pdfwrite",
    "pdfimage8",
    "pdfimage24",
    "pdfimage32",
)

BANNED_BINARY_TOOLS: tuple[tuple[str, str], ...] = (
    ("mutool", "clean"),
    ("mutool", "create"),
    ("mutool", "merge"),
    ("qpdf", "--encrypt"),  # qpdf in encrypt mode writes a new PDF
    ("qpdf", "--linearize"),  # likewise linearize
    ("qpdf", "--remove-page-labels"),  # likewise
    ("cpdf", ""),
)

# Modules that may import pikepdf/pymupdf for read paths. Anything
# outside this list importing those packages is a violation.
PIKEPDF_PYMUPDF_READ_ALLOWLIST: frozenset[str] = frozenset(
    {
        "codex_pdf.extract",
        "codex_pdf.extract.annotations",
        "codex_pdf.extract.color",
        "codex_pdf.extract.common",
        "codex_pdf.extract.content_inventory",
        "codex_pdf.extract.document",
        "codex_pdf.extract.fonts",
        "codex_pdf.extract.probe",
        "codex_pdf.extract.forms",
        "codex_pdf.extract.images",
        "codex_pdf.extract.ocg",
        "codex_pdf.extract.signals",
        "codex_pdf.extract.structure",
        "codex_pdf.extract.text_regions",
        "codex_pdf.extract.transparency",
        "codex_pdf.extract.trapping",
        "codex_pdf.eval.ps_type4",
        "codex_pdf.preflight_ingest.adapters",
        "codex_pdf.render._common",
        "codex_pdf.render.content_stream",
        "codex_pdf.render.layer",
        "codex_pdf.render.page",
        "codex_pdf.render.separations",
        "codex_pdf.api.main",  # imports OCGError, get_page_count, etc.
        "codex_pdf.api.url_ingest",  # PDF magic-byte sniff (no parser use)
        "codex_pdf.parity",
        "codex_pdf.cli",
        # AI Signal lane (Phase 1, 1.11.0): the dispatcher rasterises
        # pages with fitz for vision extractors and reads page bbox /
        # dimensions. Read-only; no Pdf.save anywhere in the lane.
        "codex_pdf.ai.dispatcher",
    }
)

# Functions where ``pdf.save(...)`` is allowed because the bytes are
# transient (fed to Ghostscript) and never returned to a caller.
ALLOWED_SAVE_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("codex_pdf.render._common", "apply_ocg_overrides"),
    }
)


# --- AST helpers ----------------------------------------------------------


def _attr_chain(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        prefix = _attr_chain(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _module_name(path: Path) -> str:
    """Best-effort module name from a path under ``src/``.

    Falls back to a pseudo-name rooted at the ``codex_pdf`` directory
    whenever the path lives outside the canonical source tree (the
    test fixtures live in tmp_path, not ``codex-pdf/src/codex_pdf/``).
    """
    parts = path.with_suffix("").parts
    if "codex_pdf" in parts:
        idx = parts.index("codex_pdf")
        parts = parts[idx:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _enclosing_def(tree: ast.AST, line: int) -> str | None:
    """Return the innermost def / async-def / class name covering ``line``."""
    candidate: tuple[str, int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= line <= end:
                if candidate is None or (start >= candidate[1] and end <= candidate[2]):
                    candidate = (node.name, start, end)
    return candidate[0] if candidate else None


# --- Scanners -------------------------------------------------------------


def _scan_imports(tree: ast.AST, module: str, source: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    raw_lines = source.splitlines()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [node.module]
        for name in names:
            head = name.split(".")[0]
            if head in BANNED_WRITER_MODULES:
                snippet = (
                    raw_lines[node.lineno - 1].strip() if node.lineno - 1 < len(raw_lines) else ""
                )
                out.append(
                    {
                        "kind": "banned-writer-import",
                        "module": name,
                        "line": node.lineno,
                        "snippet": snippet,
                    }
                )
            if head in {"pikepdf", "pymupdf", "fitz"}:
                if module not in PIKEPDF_PYMUPDF_READ_ALLOWLIST:
                    snippet = (
                        raw_lines[node.lineno - 1].strip()
                        if node.lineno - 1 < len(raw_lines)
                        else ""
                    )
                    out.append(
                        {
                            "kind": "parser-import-outside-allowlist",
                            "module": name,
                            "line": node.lineno,
                            "snippet": snippet,
                        }
                    )
    return out


def _scan_calls(tree: ast.AST, module: str, source: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    raw_lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _attr_chain(node.func)
        snippet = (
            raw_lines[node.lineno - 1].strip() if node.lineno - 1 < len(raw_lines) else ""
        )
        if chain in {"pikepdf.new", "pikepdf.Pdf.new"}:
            out.append(
                {
                    "kind": "pikepdf.new",
                    "module": module,
                    "line": node.lineno,
                    "snippet": snippet,
                }
            )
        if chain == "Pdf.save_bytes" or chain.endswith(".save_bytes"):
            out.append(
                {
                    "kind": "pdf.save_bytes",
                    "module": module,
                    "line": node.lineno,
                    "snippet": snippet,
                }
            )
        if chain.endswith(".save") and not chain.endswith("Image.save"):
            # Distinguish ``pdf.save(buf)`` from ``Image.save(buf, "PNG")``.
            # Image.save is fine — it writes PNG bytes, not PDF bytes.
            # Heuristic: if any argument is a string literal "PNG" /
            # "JPEG" etc., treat it as a Pillow call.
            is_pillow = any(
                isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.upper() in {"PNG", "JPEG", "JPG", "GIF", "BMP", "TIFF", "WEBP"}
                for arg in node.args
            )
            if any(
                isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                and kw.value.value.upper() in {"PNG", "JPEG", "JPG", "GIF", "BMP", "TIFF", "WEBP"}
                for kw in node.keywords
            ):
                is_pillow = True
            if is_pillow:
                continue
            enclosing = _enclosing_def(tree, node.lineno)
            if enclosing and (module, enclosing) in ALLOWED_SAVE_SITES:
                continue
            out.append(
                {
                    "kind": "pdf.save",
                    "module": module,
                    "function": enclosing,
                    "line": node.lineno,
                    "snippet": snippet,
                }
            )
        if chain in {
            "subprocess.run",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_output",
            "subprocess.check_call",
        } and node.args:
            first = node.args[0]
            if isinstance(first, ast.List):
                argv_strings = [
                    elt.value
                    for elt in first.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if argv_strings:
                    head = argv_strings[0]
                    for tool, sub in BANNED_BINARY_TOOLS:
                        if head == tool and (not sub or sub in argv_strings):
                            out.append(
                                {
                                    "kind": "banned-binary-tool",
                                    "tool": tool,
                                    "subcommand": sub,
                                    "module": module,
                                    "line": node.lineno,
                                    "snippet": snippet,
                                }
                            )
                    if head == "gs":
                        for arg in argv_strings:
                            for device in BANNED_GHOSTSCRIPT_DEVICES:
                                if arg == f"-sDEVICE={device}":
                                    out.append(
                                        {
                                            "kind": "ghostscript-pdf-writer",
                                            "device": device,
                                            "module": module,
                                            "line": node.lineno,
                                            "snippet": snippet,
                                        }
                                    )
    return out


def _scan_pdf_magic(tree: ast.AST, module: str, source: str) -> list[dict[str, object]]:
    """Flag b"%PDF-" being written to disk or concatenated into output.

    Read-only uses (`startswith(b"%PDF-")`, `data[:5] == b"%PDF-"`,
    `pdf_magic = b"%PDF-"` for sniffing) are allowed.
    """
    out: list[dict[str, object]] = []
    raw_lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
            continue
        operands = [node.left, node.right]
        for operand in operands:
            if isinstance(operand, ast.Constant) and isinstance(operand.value, (bytes, str)):
                if isinstance(operand.value, bytes) and operand.value.startswith(b"%PDF-"):
                    snippet = (
                        raw_lines[node.lineno - 1].strip()
                        if node.lineno - 1 < len(raw_lines)
                        else ""
                    )
                    out.append(
                        {
                            "kind": "pdf-magic-concat",
                            "module": module,
                            "line": node.lineno,
                            "snippet": snippet,
                        }
                    )
                if isinstance(operand.value, str) and operand.value.startswith("%PDF-"):
                    snippet = (
                        raw_lines[node.lineno - 1].strip()
                        if node.lineno - 1 < len(raw_lines)
                        else ""
                    )
                    out.append(
                        {
                            "kind": "pdf-magic-concat",
                            "module": module,
                            "line": node.lineno,
                            "snippet": snippet,
                        }
                    )
    return out


def _safe_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def audit_file(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "path": _safe_relative(path),
            "status": "FAIL",
            "violations": [{"kind": "parse-error", "detail": str(exc)}],
        }
    module = _module_name(path)
    violations: list[dict[str, object]] = []
    violations.extend(_scan_imports(tree, module, source))
    violations.extend(_scan_calls(tree, module, source))
    violations.extend(_scan_pdf_magic(tree, module, source))
    return {
        "path": _safe_relative(path),
        "module": module,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


def build_report() -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "__init__.py" and path.parent == SRC:
            continue
        result = audit_file(path)
        if result["violations"]:
            files.append(result)
    overall = "PASS" if all(item["status"] != "FAIL" for item in files) else "FAIL"
    return {
        "schema_version": "1.0.0",
        "report_kind": "codex-pdf.producer-surface-audit",
        "banned_writer_modules": list(BANNED_WRITER_MODULES),
        "banned_ghostscript_devices": list(BANNED_GHOSTSCRIPT_DEVICES),
        "allowed_save_sites": [
            {"module": m, "function": f} for (m, f) in sorted(ALLOWED_SAVE_SITES)
        ],
        "parser_read_allowlist": sorted(PIKEPDF_PYMUPDF_READ_ALLOWLIST),
        "files": files,
        "status": overall,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=None, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    report = build_report()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        out = Path(args.json).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
