"""Tests for the Separation tint-transform evaluator + extractor wiring."""

from __future__ import annotations

import pikepdf

from codex_pdf.color.alt_space import alt_to_swatch, evaluate_function
from codex_pdf.color.resolver import CodexSpotIntent, resolve_spot_swatch_color
from codex_pdf.extract.color import extract_color_space
from codex_pdf.extract.summary import _normalize_color_component, _to_u8


def _type2_dict(c0: list[float], c1: list[float], n: float = 1.0) -> dict[str, object]:
    """Plain dict mimicking a Type 2 function — sufficient for the evaluator,
    avoids the pikepdf "object inside closed Pdf" lifetime trap."""
    return {
        "/FunctionType": 2,
        "/Domain": [0, 1],
        "/C0": c0,
        "/C1": c1,
        "/N": n,
    }


def test_evaluate_type2_at_endpoint() -> None:
    fn = _type2_dict([0, 0, 0, 0], [0, 0, 0, 1])
    assert evaluate_function(fn, 1.0) == [0.0, 0.0, 0.0, 1.0]


def test_evaluate_type2_at_midpoint_linear() -> None:
    fn = _type2_dict([0, 0, 0, 0], [0, 0, 0, 1])
    out = evaluate_function(fn, 0.5)
    assert out is not None and out[3] == 0.5


def test_evaluate_type2_with_gamma_at_endpoint() -> None:
    # Gamma N=2 at t=1 still hits C1 (1^2 == 1).
    fn = _type2_dict([0, 0, 0, 0], [0.5, 0, 0, 0], n=2.0)
    out = evaluate_function(fn, 1.0)
    assert out is not None and out[0] == 0.5


def test_evaluate_type3_dispatches_to_subfunction() -> None:
    sub_a = _type2_dict([0, 0, 0, 0], [0.25, 0, 0, 0])
    sub_b = _type2_dict([0, 0, 0, 0], [0.75, 0, 0, 0])
    stitching = {
        "/FunctionType": 3,
        "/Domain": [0, 1],
        "/Bounds": [0.5],
        "/Encode": [0, 1, 0, 1],
        "/Functions": [sub_a, sub_b],
    }
    # x=0.25 lives in sub_a's domain [0, 0.5] which encodes to [0, 1]:
    # x_sub = 0.5 → midpoint of sub_a → C0 + 0.5 * (C1 - C0) = 0.125.
    assert evaluate_function(stitching, 0.25) == [0.125, 0.0, 0.0, 0.0]
    # x=1.0 lives in sub_b's domain [0.5, 1] which encodes to [0, 1]:
    # x_sub = 1.0 → endpoint of sub_b → C1 = 0.75.
    assert evaluate_function(stitching, 1.0) == [0.75, 0.0, 0.0, 0.0]


def test_evaluate_unsupported_type_returns_none() -> None:
    """Type 4 (PostScript) is intentionally unimplemented for 1.7.0;
    callers fall through to the next swatch tier."""
    assert evaluate_function({"/FunctionType": 4, "/Domain": [0, 1]}, 1.0) is None
    assert evaluate_function({"/FunctionType": 0, "/Domain": [0, 1]}, 1.0) is None


def test_alt_to_swatch_devicecmyk_returns_unit_floats() -> None:
    rgb, lab, cmyk = alt_to_swatch([0.0, 0.0, 0.0, 1.0], "DeviceCMYK")
    assert lab is None
    assert cmyk == (0.0, 0.0, 0.0, 1.0)
    assert rgb is not None and all(0.0 <= v <= 1.0 for v in rgb)
    assert max(rgb) < 0.05


def test_alt_to_swatch_lab_passes_through_with_red_dominant_rgb() -> None:
    rgb, lab, cmyk = alt_to_swatch([49.0, 75.0, 51.0], "Lab")
    assert lab == (49.0, 75.0, 51.0)
    assert cmyk is None
    assert rgb is not None
    assert rgb[0] > rgb[1] and rgb[0] > rgb[2]


def test_alt_to_swatch_iccbased_uses_components_hint() -> None:
    rgb, _, cmyk = alt_to_swatch([0.0, 0.0, 0.0, 1.0], "ICCBased", icc_components=4)
    assert cmyk == (0.0, 0.0, 0.0, 1.0)
    assert rgb is not None
    rgb2, _, _ = alt_to_swatch([0.5, 0.0, 0.0], "ICCBased", icc_components=3)
    assert rgb2 == (0.5, 0.0, 0.0)


def test_alt_to_swatch_unsupported_returns_none() -> None:
    assert alt_to_swatch([], "DeviceCMYK") == (None, None, None)
    assert alt_to_swatch([0.0, 0.0], "DeviceRGB") == (None, None, None)


def _build_separation_pdf(
    spot_name: str, alt: pikepdf.Object, c0: list[float], c1: list[float]
) -> tuple[pikepdf.Pdf, pikepdf.Array]:
    """Return ``(pdf, separation-array)``. Caller must keep ``pdf`` alive."""
    pdf = pikepdf.new()
    fn = pdf.make_indirect(
        pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array(c0),
            C1=pikepdf.Array(c1),
            N=1,
        )
    )
    arr = pikepdf.Array(
        [pikepdf.Name("/Separation"), pikepdf.Name(f"/{spot_name}"), alt, fn]
    )
    return pdf, arr


def test_extract_separation_populates_alt_intent_for_pikepdf_array() -> None:
    """pikepdf.Array used to fail isinstance(value, list) and skip extraction.

    Regression guard for the 1.7.0 fix that switched to a duck-typed
    array check so Separation/DeviceN intent actually reaches the
    spot resolver.
    """
    pdf, arr = _build_separation_pdf(
        "Black Black", pikepdf.Name("/DeviceCMYK"), [0, 0, 0, 0], [0, 0, 0, 1]
    )
    try:
        cs = extract_color_space(arr, "CS0")
        assert cs is not None
        assert cs.family == "Separation"
        assert cs.alternate_space_id == "DeviceCMYK"
        assert len(cs.spot_colorants) == 1
        colorant = cs.spot_colorants[0]
        assert colorant.name == "Black Black"
        assert colorant.cmyk == (0.0, 0.0, 0.0, 1.0)
        assert colorant.rgb is not None and max(colorant.rgb) < 0.05
    finally:
        pdf.close()


def test_resolver_picks_up_evaluated_intent_for_black_black() -> None:
    """The 1.6.x bug: 'Black Black' fell through to ``hash`` and rendered
    green. With alt-space evaluation feeding ``CodexSpotIntent`` via the
    schema, the resolver returns near-black with ``codex`` provenance.
    """
    pdf, arr = _build_separation_pdf(
        "Black Black", pikepdf.Name("/DeviceCMYK"), [0, 0, 0, 0], [0, 0, 0, 1]
    )
    try:
        cs = extract_color_space(arr, "CS0")
        assert cs is not None
        colorant = cs.spot_colorants[0]
        assert colorant.rgb is not None and colorant.cmyk is not None
        norm_rgb = tuple(_to_u8(_normalize_color_component(v)) for v in colorant.rgb)
        norm_cmyk = tuple(_normalize_color_component(v) for v in colorant.cmyk)
        intent = CodexSpotIntent(rgb=norm_rgb, cmyk=norm_cmyk)
        resolved = resolve_spot_swatch_color("Black Black", codex_intent=intent)
        assert resolved.source == "codex"
        assert resolved.rgb == (0, 0, 0)
    finally:
        pdf.close()


def test_extract_separation_with_lab_alt_records_lab() -> None:
    pdf, arr = _build_separation_pdf(
        "PANTONE 185 C", pikepdf.Name("/Lab"), [100, 0, 0], [49, 75, 51]
    )
    try:
        cs = extract_color_space(arr, "CS1")
        assert cs is not None
        colorant = cs.spot_colorants[0]
        assert colorant.lab == (49.0, 75.0, 51.0)
        assert colorant.cmyk is None
        assert colorant.rgb is not None
        r, g, b = colorant.rgb
        assert r > g and r > b
    finally:
        pdf.close()


def test_extract_separation_unsupported_function_leaves_intent_empty() -> None:
    """Type 4 PostScript falls through; colorant survives with no intent."""
    pdf = pikepdf.new()
    try:
        fn = pdf.make_indirect(
            pikepdf.Dictionary(
                FunctionType=4,
                Domain=pikepdf.Array([0, 1]),
                Range=pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1]),
            )
        )
        arr = pikepdf.Array(
            [
                pikepdf.Name("/Separation"),
                pikepdf.Name("/Custom Spot"),
                pikepdf.Name("/DeviceCMYK"),
                fn,
            ]
        )
        cs = extract_color_space(arr, "CS2")
        assert cs is not None
        colorant = cs.spot_colorants[0]
        assert colorant.rgb is None
        assert colorant.cmyk is None
    finally:
        pdf.close()


def test_extract_devicen_records_names_without_intent() -> None:
    """DeviceN's multi-input tint transform is out of scope for 1.7.0
    — names captured, intent stays empty, resolver falls through to
    its existing curated/hash chain."""
    pdf = pikepdf.new()
    try:
        fn = pdf.make_indirect(
            pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 0, 0]),
                N=1,
            )
        )
        arr = pikepdf.Array(
            [
                pikepdf.Name("/DeviceN"),
                pikepdf.Array([pikepdf.Name("/Cyan"), pikepdf.Name("/Magenta")]),
                pikepdf.Name("/DeviceCMYK"),
                fn,
            ]
        )
        cs = extract_color_space(arr, "CS3")
        assert cs is not None
        assert cs.family == "DeviceN"
        names = [c.name for c in cs.spot_colorants]
        assert names == ["Cyan", "Magenta"]
        assert all(c.rgb is None for c in cs.spot_colorants)
    finally:
        pdf.close()
