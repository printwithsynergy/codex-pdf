from codex_pdf.parity import (
    compare_deep,
    compare_inventory,
    compare_summary,
    codex_deep_projection,
    codex_inventory_projection,
    codex_summary_projection,
)


def test_summary_projection_shape() -> None:
    payload = {
        "pdf_version": "1.7",
        "is_encrypted": False,
        "pages": [
            {
                "page_num": 1,
                "rotation": 0,
                "boxes": {
                    "media": {"x0": 0, "y0": 0, "x1": 612, "y1": 792},
                },
            }
        ],
        "info": {"title": "Fixture"},
    }
    out = codex_summary_projection(payload)
    assert out["page_count"] == 1
    assert out["pages"][0]["media_box"] == [0.0, 0.0, 612.0, 792.0]


def test_inventory_projection_shape() -> None:
    payload = {
        "pdf_version": "1.7",
        "is_encrypted": False,
        "pages": [{"page_num": 1, "inventory": [{}, {}]}],
        "fonts": [{}],
        "images": [{"page_num": 1}],
        "annotations": [{"page_num": 1}],
        "color_spaces": [],
        "icc_profiles": [],
        "ocgs": [],
        "form_xobjects": [],
    }
    out = codex_inventory_projection(payload)
    assert out["inventory"]["fonts"] == 1
    assert out["page_inventory"][0]["objects"] == 2
    assert out["page_inventory"][0]["images"] == 1


def test_compare_summary_no_diffs() -> None:
    baseline = {
        "pdf_version": "1.7",
        "page_count": 1,
        "is_encrypted": False,
        "pages": [
            {
                "page_num": 1,
                "rotate": 0,
                "media_box": [0.0, 0.0, 612.0, 792.0],
                "crop_box": [0.0, 0.0, 612.0, 792.0],
                "bleed_box": [0.0, 0.0, 612.0, 792.0],
                "trim_box": [0.0, 0.0, 612.0, 792.0],
                "art_box": [0.0, 0.0, 612.0, 792.0],
            }
        ],
    }
    codex = dict(baseline)
    assert compare_summary(baseline, codex) == []


def test_compare_inventory_detects_diffs() -> None:
    baseline = {
        "pdf_version": "1.7",
        "page_count": 1,
        "is_encrypted": False,
        "inventory": {"fonts": 1, "images": 0, "annotations": 0, "color_spaces": 0, "icc_profiles": 0, "ocgs": 0, "form_xobjects": 0},
        "page_inventory": [{"page_num": 1, "objects": 0, "images": 0, "annotations": 0}],
    }
    codex = {
        "pdf_version": "1.7",
        "page_count": 1,
        "is_encrypted": False,
        "inventory": {"fonts": 2, "images": 0, "annotations": 0, "color_spaces": 0, "icc_profiles": 0, "ocgs": 0, "form_xobjects": 0},
        "page_inventory": [{"page_num": 1, "objects": 0, "images": 0, "annotations": 0}],
    }
    diffs = compare_inventory(baseline, codex)
    assert any(d.field == "inventory.fonts" for d in diffs)


def test_deep_projection_shape() -> None:
    payload = {
        "pdf_version": "1.7",
        "trapped_flag": "Unknown",
        "conformance": {"pdfx": "unknown", "pdfa": None, "pdfua": None},
        "pages": [{}],
        "output_intents": [{}],
        "color_spaces": [{}],
        "fonts": [{}],
        "images": [],
        "ocgs": [],
        "form_xobjects": [],
        "annotations": [],
        "preflight_reports": [],
        "trap_evidence": {"trap_layers": []},
    }
    out = codex_deep_projection(payload)
    assert out["counts"]["pages"] == 1
    assert out["counts"]["output_intents"] == 1


def test_compare_deep_detects_count_diffs() -> None:
    baseline = {"pdf_version": "1.7", "trapped_flag": "Unknown", "counts": {"pages": 1, "fonts": 1}}
    codex = {"pdf_version": "1.7", "trapped_flag": "Unknown", "counts": {"pages": 1, "fonts": 2}}
    diffs = compare_deep(baseline, codex)
    assert any(d.field == "counts.fonts" for d in diffs)
