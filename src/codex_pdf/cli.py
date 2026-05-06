"""codex-pdf CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import validate

from codex_pdf.extract import extract_from_path
from codex_pdf.parity import run_parity_from_namespace
from codex_pdf.schema import codex_document_schema, load_published_schema


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _write_json(payload: Any, *, pretty: bool, output_path: str | None) -> None:
    if pretty:
        text = json.dumps(payload, indent=2, sort_keys=True)
    else:
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if output_path:
        Path(output_path).write_text(f"{text}\n", encoding="utf-8")
    else:
        print(text)


def _published_schema_path() -> Path:
    return _repo_root() / "schemas" / "v1" / "codex-document.schema.json"


def _contract_manifest() -> dict[str, Any]:
    return {
        "contract_name": "codex-document",
        "schema_version": "1.0.0",
        "schema_path": str(_published_schema_path()),
        "schema_id": "https://schemas.thinkneverland.com/codex-pdf/v1/codex-document.schema.json",
        "extract_command": "codex-pdf extract <input_pdf>",
        "validate_command": "codex-pdf validate <codex_json>",
    }


def cmd_extract(args: argparse.Namespace) -> int:
    doc = extract_from_path(Path(args.input_pdf))
    payload = doc.model_dump(mode="json")
    _write_json(payload, pretty=args.pretty, output_path=args.output)
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    if args.published:
        schema = load_published_schema(_repo_root())
    else:
        schema = codex_document_schema()
    _write_json(schema, pretty=True, output_path=args.output)
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    _write_json(_contract_manifest(), pretty=True, output_path=args.output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.codex_json).read_text(encoding="utf-8"))
    schema = load_published_schema(_repo_root())
    validate(payload, schema)
    print("valid")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    doc = extract_from_path(Path(args.input_pdf))
    result = {
        "pdf_version": doc.pdf_version,
        "page_count": len(doc.pages),
        "is_encrypted": doc.is_encrypted,
        "output_intents": [x.model_dump(mode="json") for x in doc.output_intents],
        "document_id": doc.document_id,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"pdf_version={result['pdf_version']} "
            f"page_count={result['page_count']} "
            f"is_encrypted={result['is_encrypted']}"
        )
    return 0


def cmd_parity(args: argparse.Namespace) -> int:
    return run_parity_from_namespace(args, _repo_root())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-pdf")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="Extract a CodexDocument from a PDF.")
    extract.add_argument("input_pdf")
    extract.add_argument("--pretty", action="store_true")
    extract.add_argument("--output", default=None, help="Write JSON to this path instead of stdout.")
    extract.set_defaults(func=cmd_extract)

    schema = sub.add_parser("schema", help="Print JSON Schema for CodexDocument.")
    schema.add_argument("--version", default="1")
    schema.add_argument("--name", default="codex-document")
    schema.add_argument("--published", action="store_true")
    schema.add_argument("--output", default=None, help="Write schema JSON to this path.")
    schema.set_defaults(func=cmd_schema)

    contract = sub.add_parser("contract", help="Print machine-readable codex contract manifest.")
    contract.add_argument("--output", default=None, help="Write contract JSON to this path.")
    contract.set_defaults(func=cmd_contract)

    validate_cmd = sub.add_parser("validate", help="Validate codex JSON against published schema.")
    validate_cmd.add_argument("codex_json")
    validate_cmd.set_defaults(func=cmd_validate)

    probe = sub.add_parser("probe", help="Fast metadata probe.")
    probe.add_argument("input_pdf")
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(func=cmd_probe)

    parity = sub.add_parser("parity", help="Run consumer-agnostic parity projection checks.")
    parity.add_argument("--profile", choices=["summary", "inventory", "deep"], default="summary")
    parity.add_argument("--fixtures-root", required=True, help="Path to fixture corpus root.")
    parity.add_argument(
        "--output",
        default=str(_repo_root() / "reports" / "parity" / "viewer_essentials.json"),
        help="Path to write parity JSON report.",
    )
    parity.add_argument("--max-files", type=int, default=10, help="Limit number of PDFs.")
    parity.add_argument(
        "--baseline-command",
        default=None,
        help=(
            "Optional shell command template that prints JSON projection to stdout. "
            "Use {pdf} placeholder for input path."
        ),
    )
    parity.add_argument("--fail-on-diff", action="store_true", help="Return non-zero if any diff exists.")
    parity.set_defaults(func=cmd_parity)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
