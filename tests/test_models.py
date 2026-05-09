from codex_pdf.models.v1 import CodexBBox, CodexDocument, CodexPage, CodexPageBoxes, CodexSourceRef


def test_document_minimum_shape() -> None:
    doc = CodexDocument(
        codex_version="0.1.0",
        document_id="abc",
        source=CodexSourceRef(uri="memory://fixture"),
        pages=[
            CodexPage(
                page_num=1,
                boxes=CodexPageBoxes(media=CodexBBox(x0=0, y0=0, x1=100, y1=200)),
            )
        ],
    )
    payload = doc.model_dump(mode="json")
    assert payload["schema_version"] == "1.1.0"
    assert payload["pages"][0]["page_num"] == 1
