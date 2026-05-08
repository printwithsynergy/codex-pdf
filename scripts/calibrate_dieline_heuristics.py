#!/usr/bin/env python3
"""Synthetic dieline calibration harness.

Computes false-positive/recall metrics for the canonical detector and writes a
machine-readable report consumed by CI tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from codex_pdf.extract.summary import build_document_summary
from codex_pdf.models.v1 import CodexDocument, CodexOCG, CodexSourceRef


def _doc_with_analysis(analysis: dict) -> CodexDocument:
    return CodexDocument(
        codex_version="1.4.0",
        document_id="calibration",
        source=CodexSourceRef(uri="calibration.pdf", sha256="calibration", size_bytes=1),
        analysis=analysis,
    )


def _negative_structural_ops() -> list[dict[str, object]]:
    ops: list[dict[str, object]] = []
    for _ in range(8):
        ops.append({"op": "m", "operands": [0, 0]})
        ops.append({"op": "l", "operands": [20, 20]})
        ops.append({"op": "f", "operands": []})
    return ops


def _positive_structural_ops() -> list[dict[str, object]]:
    ops: list[dict[str, object]] = []
    for _ in range(30):
        ops.append({"op": "m", "operands": [0, 0]})
        ops.append({"op": "l", "operands": [100, 0]})
    for _ in range(10):
        ops.append({"op": "S", "operands": []})
    ops.extend(
        [
            {"op": "w", "operands": [0.4]},
            {"op": "d", "operands": [[2, 2], 0]},
            {"op": "d", "operands": [[3, 1], 0]},
        ]
    )
    return ops


def build_report() -> dict[str, object]:
    negatives: list[CodexDocument] = []
    positives: list[CodexDocument] = []
    for idx in range(50):
        doc = _doc_with_analysis({"page_1": {"content_ops": _negative_structural_ops()}})
        if idx % 7 == 0:
            doc.ocgs = [CodexOCG(ocg_id=f"n{idx}", name="Artwork Layer")]
        negatives.append(doc)
    for idx in range(25):
        doc = _doc_with_analysis({"page_1": {"content_ops": _positive_structural_ops()}})
        if idx % 5 == 0:
            doc.ocgs = [CodexOCG(ocg_id=f"p{idx}", name="Dieline")]
        positives.append(doc)

    negative_hits = sum(1 for doc in negatives if build_document_summary(doc).dieline.count > 0)
    positive_hits = sum(1 for doc in positives if build_document_summary(doc).dieline.count > 0)
    false_positive_rate = negative_hits / len(negatives)
    recall = positive_hits / len(positives)
    return {
        "dataset": "synthetic-structural-v1",
        "negative_samples": len(negatives),
        "positive_samples": len(positives),
        "negative_hits": negative_hits,
        "positive_hits": positive_hits,
        "false_positive_rate": round(false_positive_rate, 4),
        "recall": round(recall, 4),
        "budget_false_positive_max": 0.03,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_file = root / "reports" / "dieline_calibration_report.json"
    report = build_report()
    out_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
