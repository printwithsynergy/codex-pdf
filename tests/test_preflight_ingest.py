from codex_pdf.preflight_ingest import parse_preflight_report


def test_ingest_lintpdf_json() -> None:
    payload = {
        "engine_version": "1.2.3",
        "findings": [
            {
                "inspection_id": "LPDF_001",
                "severity": "error",
                "message": "Issue",
                "page_num": 1,
                "source": "lintpdf",
            }
        ],
    }
    report = parse_preflight_report(str(payload).replace("'", '"'), "lintpdf_json")
    assert report.source_engine == "lintPDF"
    assert len(report.issues) == 1
    assert report.issues[0].severity == "error"


def test_ingest_callas_xml() -> None:
    xml = "<report><issues><issue severity='warning' page='2' rule='R1' message='Warn'/></issues></report>"
    report = parse_preflight_report(xml, "callas_xml")
    assert report.source_engine == "callas"
    assert len(report.issues) == 1
    assert report.issues[0].page_num == 2


def test_ingest_pitstop_xml() -> None:
    xml = "<pitstop><issue severity='error' page='1' id='PS1' message='Fail'/></pitstop>"
    report = parse_preflight_report(xml, "pitstop_xml")
    assert report.source_engine == "PitStop"
    assert report.issues[0].severity == "error"


def test_ingest_acrobat_xml() -> None:
    xml = "<acrobat><issue severity='advisory' page='3' check='A1' message='Info'/></acrobat>"
    report = parse_preflight_report(xml, "acrobat_xml")
    assert report.source_engine == "Acrobat"
    assert report.issues[0].severity == "advisory"
