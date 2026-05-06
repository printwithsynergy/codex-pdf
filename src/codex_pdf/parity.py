"""Consumer-agnostic parity runner for codex projections."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from codex_pdf.extract.document import extract_document


@dataclass
class Diff:
    field: str
    baseline: Any
    codex: Any


def run_baseline_command(command_template: str, pdf_path: Path) -> dict[str, Any]:
    command = command_template.replace("{pdf}", str(pdf_path))
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"baseline extraction failed for {pdf_path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout.strip())


def box_to_list(box: Any) -> list[float] | None:
    if not isinstance(box, dict):
        return None
    try:
        return [float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"])]
    except (TypeError, ValueError, KeyError):
        return None


def codex_summary_projection(payload: dict[str, Any]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    for page in payload.get("pages", []):
        boxes = page.get("boxes") or {}
        media = boxes.get("media")
        crop = boxes.get("crop") or media
        bleed = boxes.get("bleed") or media
        trim = boxes.get("trim") or media
        art = boxes.get("art") or media
        pages.append(
            {
                "page_num": int(page.get("page_num", 0) or 0),
                "rotate": int(page.get("rotation", 0) or 0),
                "user_unit": 1.0,
                "media_box": box_to_list(media),
                "crop_box": box_to_list(crop),
                "bleed_box": box_to_list(bleed),
                "trim_box": box_to_list(trim),
                "art_box": box_to_list(art),
                "width_pts": (float(media["x1"]) - float(media["x0"])) if isinstance(media, dict) else 0.0,
                "height_pts": (float(media["y1"]) - float(media["y0"])) if isinstance(media, dict) else 0.0,
            }
        )
    return {
        "pdf_version": payload.get("pdf_version"),
        "page_count": len(pages),
        "is_encrypted": bool(payload.get("is_encrypted", False)),
        "pages": pages,
        "info_dict": payload.get("info") or {},
    }


def codex_inventory_projection(payload: dict[str, Any]) -> dict[str, Any]:
    page_images: dict[int, int] = {}
    for image in payload.get("images", []):
        if not isinstance(image, dict):
            continue
        page_num = int(image.get("page_num", 0) or 0)
        page_images[page_num] = page_images.get(page_num, 0) + 1

    page_annotations: dict[int, int] = {}
    for ann in payload.get("annotations", []):
        if not isinstance(ann, dict):
            continue
        page_num = int(ann.get("page_num", 0) or 0)
        page_annotations[page_num] = page_annotations.get(page_num, 0) + 1

    page_inventory: list[dict[str, Any]] = []
    for page in payload.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_num = int(page.get("page_num", 0) or 0)
        page_inventory.append(
            {
                "page_num": page_num,
                "objects": len(page.get("inventory", []) or []),
                "images": page_images.get(page_num, 0),
                "annotations": page_annotations.get(page_num, 0),
            }
        )

    return {
        "pdf_version": payload.get("pdf_version"),
        "page_count": len(payload.get("pages", []) or []),
        "is_encrypted": bool(payload.get("is_encrypted", False)),
        "inventory": {
            "fonts": len(payload.get("fonts", []) or []),
            "images": len(payload.get("images", []) or []),
            "annotations": len(payload.get("annotations", []) or []),
            "color_spaces": len(payload.get("color_spaces", []) or []),
            "icc_profiles": len(payload.get("icc_profiles", []) or []),
            "ocgs": len(payload.get("ocgs", []) or []),
            "form_xobjects": len(payload.get("form_xobjects", []) or []),
        },
        "page_inventory": page_inventory,
    }


def codex_deep_projection(payload: dict[str, Any]) -> dict[str, Any]:
    def first_or_none(value: Any) -> Any:
        if isinstance(value, list) and len(value) > 0:
            return value[0]
        return None

    return {
        "pdf_version": payload.get("pdf_version"),
        "trapped_flag": payload.get("trapped_flag"),
        "conformance": payload.get("conformance", {}),
        "counts": {
            "pages": len(payload.get("pages", []) or []),
            "output_intents": len(payload.get("output_intents", []) or []),
            "color_spaces": len(payload.get("color_spaces", []) or []),
            "fonts": len(payload.get("fonts", []) or []),
            "images": len(payload.get("images", []) or []),
            "ocgs": len(payload.get("ocgs", []) or []),
            "form_xobjects": len(payload.get("form_xobjects", []) or []),
            "annotations": len(payload.get("annotations", []) or []),
            "preflight_reports": len(payload.get("preflight_reports", []) or []),
        },
        "sample_output_intent": first_or_none(payload.get("output_intents")),
        "sample_color_space": first_or_none(payload.get("color_spaces")),
        "sample_ocg": first_or_none(payload.get("ocgs")),
        "trap_evidence": payload.get("trap_evidence", {}),
    }


def codex_projection(profile: Literal["summary", "inventory", "deep"], pdf_path: Path) -> dict[str, Any]:
    payload = extract_document(pdf_path.read_bytes(), source_uri=str(pdf_path)).model_dump(mode="json")
    if profile == "summary":
        return codex_summary_projection(payload)
    if profile == "inventory":
        return codex_inventory_projection(payload)
    return codex_deep_projection(payload)


def compare_summary(baseline: dict[str, Any], codex: dict[str, Any]) -> list[Diff]:
    diffs: list[Diff] = []
    for field in ("pdf_version", "page_count", "is_encrypted"):
        if baseline.get(field) != codex.get(field):
            diffs.append(Diff(field=field, baseline=baseline.get(field), codex=codex.get(field)))

    if len(baseline.get("pages", [])) != len(codex.get("pages", [])):
        diffs.append(
            Diff(
                field="pages.length",
                baseline=len(baseline.get("pages", [])),
                codex=len(codex.get("pages", [])),
            )
        )
        return diffs

    for idx, (lp, cp) in enumerate(
        zip(baseline.get("pages", []), codex.get("pages", []), strict=False),
        start=1,
    ):
        for key in ("page_num", "rotate", "media_box", "crop_box", "bleed_box", "trim_box", "art_box"):
            if lp.get(key) != cp.get(key):
                diffs.append(Diff(field=f"pages[{idx}].{key}", baseline=lp.get(key), codex=cp.get(key)))
    return diffs


def compare_inventory(baseline: dict[str, Any], codex: dict[str, Any]) -> list[Diff]:
    diffs: list[Diff] = []
    for field in ("pdf_version", "page_count", "is_encrypted"):
        if baseline.get(field) != codex.get(field):
            diffs.append(Diff(field=field, baseline=baseline.get(field), codex=codex.get(field)))

    b_inv = baseline.get("inventory", {}) or {}
    c_inv = codex.get("inventory", {}) or {}
    for key in ("fonts", "images", "annotations", "color_spaces", "icc_profiles", "ocgs", "form_xobjects"):
        if b_inv.get(key) != c_inv.get(key):
            diffs.append(Diff(field=f"inventory.{key}", baseline=b_inv.get(key), codex=c_inv.get(key)))

    b_pages = baseline.get("page_inventory", []) or []
    c_pages = codex.get("page_inventory", []) or []
    if len(b_pages) != len(c_pages):
        diffs.append(Diff(field="page_inventory.length", baseline=len(b_pages), codex=len(c_pages)))
        return diffs
    for idx, (bp, cp) in enumerate(zip(b_pages, c_pages, strict=False), start=1):
        for key in ("page_num", "objects", "images", "annotations"):
            if bp.get(key) != cp.get(key):
                diffs.append(Diff(field=f"page_inventory[{idx}].{key}", baseline=bp.get(key), codex=cp.get(key)))
    return diffs


def compare_deep(baseline: dict[str, Any], codex: dict[str, Any]) -> list[Diff]:
    diffs: list[Diff] = []
    for field in ("pdf_version", "trapped_flag"):
        if baseline.get(field) != codex.get(field):
            diffs.append(Diff(field=field, baseline=baseline.get(field), codex=codex.get(field)))

    b_counts = baseline.get("counts", {}) or {}
    c_counts = codex.get("counts", {}) or {}
    for key in ("pages", "output_intents", "color_spaces", "fonts", "images", "ocgs", "form_xobjects", "annotations"):
        if b_counts.get(key) != c_counts.get(key):
            diffs.append(Diff(field=f"counts.{key}", baseline=b_counts.get(key), codex=c_counts.get(key)))
    return diffs


def compare(
    profile: Literal["summary", "inventory", "deep"],
    baseline: dict[str, Any],
    codex: dict[str, Any],
) -> list[Diff]:
    if profile == "summary":
        return compare_summary(baseline, codex)
    if profile == "inventory":
        return compare_inventory(baseline, codex)
    return compare_deep(baseline, codex)


def discover_fixtures(root: Path) -> list[Path]:
    return sorted(root.rglob("*.pdf"))


def run_parity(
    *,
    profile: Literal["summary", "inventory", "deep"],
    fixtures_root: Path,
    output: Path,
    max_files: int,
    baseline_command: str | None,
    fail_on_diff: bool,
) -> int:
    fixtures = discover_fixtures(fixtures_root)[:max_files]
    report: dict[str, Any] = {
        "profile": profile,
        "fixtures_root": str(fixtures_root.resolve()),
        "fixtures_total": len(fixtures),
        "baseline_command": baseline_command,
        "cases": [],
    }

    diff_count = 0
    for fixture in fixtures:
        case: dict[str, Any] = {"pdf": str(fixture), "status": "ok", "diffs": []}
        try:
            codex = codex_projection(profile, fixture)
            case["codex_projection"] = codex
            if baseline_command:
                baseline = run_baseline_command(baseline_command, fixture)
                case["baseline_projection"] = baseline
                diffs = compare(profile, baseline, codex)
                case["diffs"] = [d.__dict__ for d in diffs]
                if diffs:
                    case["status"] = "diff"
                    diff_count += len(diffs)
            else:
                case["status"] = "no_baseline"
        except Exception as exc:  # pragma: no cover
            case["status"] = "error"
            case["error"] = str(exc)
            diff_count += 1
        report["cases"].append(case)

    report["diff_count"] = diff_count
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote report: {output}")
    print(f"fixtures: {len(fixtures)} diff_count: {diff_count}")
    if fail_on_diff and diff_count > 0:
        return 2
    return 0


def run_parity_from_namespace(args: argparse.Namespace, repo_root: Path) -> int:
    return run_parity(
        profile=args.profile,
        fixtures_root=Path(args.fixtures_root),
        output=Path(args.output),
        max_files=args.max_files,
        baseline_command=args.baseline_command,
        fail_on_diff=args.fail_on_diff,
    )
