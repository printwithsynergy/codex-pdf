from codex_pdf.extract.summary import build_document_summary
from codex_pdf.models.v1 import (
    CodexColorSpace,
    CodexDocument,
    CodexSourceRef,
    CodexSpotColorant,
)


def _empty_doc() -> CodexDocument:
    return CodexDocument(
        codex_version="1.4.0",
        document_id="spot-test",
        source=CodexSourceRef(uri="fixture.pdf", sha256="spot-test", size_bytes=10),
        analysis={},
    )


def test_summary_spot_colors_uses_pantone_for_analysis_only_name() -> None:
    doc = _empty_doc()
    doc.analysis = {"spot_names": ["PANTONE 485 C"]}
    summary = build_document_summary(doc)
    spot = next(c for c in summary.spot_colors.colors if c.name == "PANTONE 485 C")
    assert spot.swatch_source == "pantone"
    assert spot.swatch_hex.startswith("#")
    assert spot.swatch_note is not None


def test_summary_spot_colors_uses_icc_alternate_when_available() -> None:
    doc = _empty_doc()
    doc.color_spaces = [
        CodexColorSpace(id="AltRGB", family="ICCBased"),
        CodexColorSpace(
            id="SpotCS",
            family="Separation",
            alternate_space_id="AltRGB",
            spot_colorants=[
                CodexSpotColorant(
                    name="Custom Spot",
                    alternate_space_id="AltRGB",
                    rgb=(0.1, 0.4, 0.7),
                )
            ],
        ),
    ]
    summary = build_document_summary(doc)
    spot = next(c for c in summary.spot_colors.colors if c.name == "Custom Spot")
    assert spot.swatch_source == "icc_alternate"
    assert spot.swatch_hex == "#1a66b2"


def test_summary_spot_colors_uses_hash_for_unknown_analysis_spot() -> None:
    doc = _empty_doc()
    doc.analysis = {"spot_names": ["Mystery Ink 42"]}
    summary = build_document_summary(doc)
    spot = next(c for c in summary.spot_colors.colors if c.name == "Mystery Ink 42")
    assert spot.swatch_source == "hash"
    assert spot.swatch_note == "Deterministic hash fallback"
