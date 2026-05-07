from __future__ import annotations

import io

import pikepdf

from codex_pdf.extract.document import extract_document


def _make_sep_cs(pdf: pikepdf.Pdf, spot_name: str) -> pikepdf.Array:
    tint = pdf.make_indirect(
        pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            Range=pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 0, 0, 1]),
            N=1,
        )
    )
    return pikepdf.Array(
        [
            pikepdf.Name("/Separation"),
            pikepdf.Name("/" + spot_name),
            pikepdf.Name("/DeviceCMYK"),
            tint,
        ]
    )


def test_extract_analysis_includes_spots_layers_and_ops() -> None:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        ColorSpace=pikepdf.Dictionary(CS_DIE=_make_sep_cs(pdf, "Dieline")),
        Properties=pikepdf.Dictionary(
            DielineOCG=pikepdf.Dictionary(Type=pikepdf.Name("/OCG"), Name="Dieline")
        ),
        ExtGState=pikepdf.Dictionary(GS_OP=pikepdf.Dictionary(OP=True)),
    )
    page.Contents = pdf.make_stream(
        b"/GS_OP gs\n/CS_DIE CS\n1 SCN\n10 10 100 100 re\nS\n/OC /DielineOCG BDC\nEMC\n"
    )
    pdf.Root["/OCProperties"] = pikepdf.Dictionary(
        OCGs=pikepdf.Array([pikepdf.Dictionary(Type=pikepdf.Name("/OCG"), Name="Dieline")])
    )
    buf = io.BytesIO()
    pdf.save(buf)

    doc = extract_document(buf.getvalue())
    analysis = doc.analysis
    assert "Dieline" in analysis.get("spot_names", [])
    assert "Dieline" in analysis.get("layer_names", [])
    page_1 = analysis.get("page_1", {})
    assert page_1.get("cs_to_spot", {}).get("CS_DIE") == "Dieline"
    assert page_1.get("prop_to_ocg_name", {}).get("DielineOCG") == "Dieline"
    assert page_1.get("extgstate", {}).get("GS_OP", {}).get("OP") is True
    ops = page_1.get("content_ops", [])
    assert any(op.get("op") == "CS" for op in ops if isinstance(op, dict))
