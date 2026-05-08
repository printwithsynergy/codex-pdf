from codex_pdf.extract.summary import build_document_summary
from codex_pdf.models.v1 import CodexDocument, CodexOCG, CodexSourceRef


def _doc_with_analysis(analysis: dict) -> CodexDocument:
    return CodexDocument(
        codex_version="1.4.0",
        document_id="deadbeef",
        source=CodexSourceRef(uri="fixture.pdf", sha256="deadbeef", size_bytes=1234),
        analysis=analysis,
    )


def test_dieline_summary_detects_name_based_candidates() -> None:
    doc = _doc_with_analysis({})
    doc.ocgs = [CodexOCG(ocg_id="oc1", name="Dieline")]
    summary = build_document_summary(doc)
    assert summary.dieline.count >= 1
    ocg_hits = [c for c in summary.dieline.candidates if c.source == "ocg_name"]
    assert ocg_hits
    assert all(c.confidence >= 0.9 for c in ocg_hits)
    assert all("name_keyword" in c.reason_codes for c in ocg_hits)
    assert summary.dieline.overall_confidence >= 0.9


def test_dieline_summary_detects_structural_candidates_without_spot_names() -> None:
    # No spot names / no OCG labels. Detection should still find likely linework.
    ops = []
    for _ in range(32):
        ops.append({"op": "m", "operands": [0, 0]})
        ops.append({"op": "l", "operands": [100, 0]})
    for _ in range(12):
        ops.append({"op": "S", "operands": []})
    ops.extend(
        [
            {"op": "w", "operands": [0.5]},
            {"op": "d", "operands": [[2, 2], 0]},
            {"op": "d", "operands": [[3, 3], 0]},
        ]
    )
    doc = _doc_with_analysis({"page_1": {"content_ops": ops}})
    summary = build_document_summary(doc)
    signal_hits = [c for c in summary.dieline.candidates if c.source == "analysis_signal"]
    assert signal_hits
    names = [c.name for c in summary.dieline.candidates]
    assert any("dieline-like" in name for name in names)
    assert any("foldline-like" in name for name in names)
    fold = next(c for c in signal_hits if "foldline-like" in c.name)
    assert fold.confidence >= 0.7
    assert "analysis_dash_pattern" in fold.reason_codes
    dieline = next(c for c in signal_hits if "dieline-like" in c.name)
    assert dieline.confidence >= 0.8
    assert "analysis_dense_path_network" in dieline.reason_codes
    assert summary.dieline.overall_confidence >= dieline.confidence


def test_dieline_summary_uses_non_first_page_analysis() -> None:
    ops = []
    for _ in range(24):
        ops.append({"op": "re", "operands": [0, 0, 10, 10]})
    for _ in range(10):
        ops.append({"op": "S", "operands": []})
    ops.extend(
        [
            {"op": "w", "operands": [0.75]},
            {"op": "d", "operands": [[1, 1], 0]},
            {"op": "d", "operands": [[1, 2], 0]},
        ]
    )
    doc = _doc_with_analysis({"page_2": {"content_ops": ops}})
    summary = build_document_summary(doc)
    page2_hit = next(c for c in summary.dieline.candidates if "page 2" in c.name)
    assert page2_hit.source == "analysis_signal"
    assert page2_hit.reason_codes


def test_dieline_summary_overall_confidence_defaults_to_zero() -> None:
    summary = build_document_summary(_doc_with_analysis({}))
    assert summary.dieline.count == 0
    assert summary.dieline.overall_confidence == 0.0
