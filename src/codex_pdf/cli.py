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
        "schema_version": "1.2.0",
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


def cmd_render_page(args: argparse.Namespace) -> int:
    """Render a single page to PNG via the codex render core."""
    from codex_pdf.render.page import render_page

    pdf_bytes = Path(args.input_pdf).read_bytes()
    on = [int(x) for x in (args.ocg_on or "").split(",") if x.strip()]
    off = [int(x) for x in (args.ocg_off or "").split(",") if x.strip()]
    png = render_page(
        pdf_bytes,
        args.page,
        dpi=args.dpi,
        ocg_on=on or None,
        ocg_off=off or None,
        simulate_overprint=args.simulate_overprint,
    )
    Path(args.output).write_bytes(png)
    return 0


def cmd_render_separations(args: argparse.Namespace) -> int:
    """Render every separation channel for one page; writes PNGs into a directory."""
    from codex_pdf.render.separations import render_separations

    pdf_bytes = Path(args.input_pdf).read_bytes()
    result = render_separations(pdf_bytes, args.page, dpi=args.dpi)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for ch in result["channels"]:
        safe = "".join(c if c.isalnum() else "_" for c in ch["name"])
        target = out_dir / f"p{args.page}_{safe}.png"
        target.write_bytes(ch["png"])
        manifest.append(
            {"name": ch["name"], "type": ch["type"], "path": str(target.relative_to(out_dir))}
        )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {"page_num": result["page_num"], "dpi": result["dpi"], "channels": manifest},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


def cmd_render_heatmap(args: argparse.Namespace) -> int:
    from codex_pdf.render.separations import render_heatmap

    pdf_bytes = Path(args.input_pdf).read_bytes()
    result = render_heatmap(pdf_bytes, args.page, dpi=args.dpi, tac_limit=args.tac_limit)
    Path(args.output).write_bytes(result["png"])
    if args.runs:
        Path(args.runs).write_text(json.dumps(result["runs"], indent=2) + "\n")
    return 0


def cmd_render_layer(args: argparse.Namespace) -> int:
    from codex_pdf.render.layer import render_layer

    pdf_bytes = Path(args.input_pdf).read_bytes()
    all_idx = [int(x) for x in args.all_layer_indices.split(",") if x.strip()]
    png = render_layer(
        pdf_bytes,
        args.page,
        layer_index=args.layer_index,
        all_layer_indices=all_idx,
        dpi=args.dpi,
    )
    Path(args.output).write_bytes(png)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the codex HTTP API (uvicorn under the hood)."""
    import uvicorn

    uvicorn.run(
        "codex_pdf.api.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


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

    render = sub.add_parser("render", help="Codex render core (page/separations/heatmap/layer).")
    render_sub = render.add_subparsers(dest="render_command", required=True)

    rp = render_sub.add_parser("page", help="Render one page to PNG.")
    rp.add_argument("input_pdf")
    rp.add_argument("--page", type=int, default=1)
    rp.add_argument("--dpi", type=int, default=300)
    rp.add_argument("--ocg-on", default=None, help="Comma-separated indices to force visible.")
    rp.add_argument("--ocg-off", default=None, help="Comma-separated indices to force hidden.")
    rp.add_argument("--no-simulate-overprint", dest="simulate_overprint", action="store_false")
    rp.set_defaults(simulate_overprint=True, func=cmd_render_page)
    rp.add_argument("--output", required=True)

    rs = render_sub.add_parser("separations", help="Render every separation channel for one page.")
    rs.add_argument("input_pdf")
    rs.add_argument("--page", type=int, default=1)
    rs.add_argument("--dpi", type=int, default=150)
    rs.add_argument("--output-dir", required=True)
    rs.set_defaults(func=cmd_render_separations)

    rh = render_sub.add_parser("heatmap", help="Render TAC heatmap PNG.")
    rh.add_argument("input_pdf")
    rh.add_argument("--page", type=int, default=1)
    rh.add_argument("--dpi", type=int, default=150)
    rh.add_argument("--tac-limit", type=float, default=300)
    rh.add_argument("--output", required=True)
    rh.add_argument("--runs", default=None, help="Optional path to write per-text-run JSON.")
    rh.set_defaults(func=cmd_render_heatmap)

    rl = render_sub.add_parser("layer", help="Render one OCG-isolated layer to RGBA PNG.")
    rl.add_argument("input_pdf")
    rl.add_argument("--page", type=int, default=1)
    rl.add_argument("--dpi", type=int, default=150)
    rl.add_argument("--layer-index", type=int, required=True)
    rl.add_argument("--all-layer-indices", required=True, help="Comma-separated indices.")
    rl.add_argument("--output", required=True)
    rl.set_defaults(func=cmd_render_layer)

    serve = sub.add_parser("serve", help="Start codex HTTP API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
