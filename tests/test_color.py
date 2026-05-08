"""Tests for the codex_pdf.color package + /v1/color/* endpoints."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from codex_pdf.api.main import app
from codex_pdf.color import (
    COLOR_SCHEMA_VERSION,
    CodexSpotIntent,
    SpotInkOverride,
    alternate_pantone_key,
    cmyk_to_srgb_naive,
    delta_e_2000,
    delta_e_76,
    lab_d50_to_srgb,
    load_inkbook,
    load_pantone_reference,
    lookup_curated_spot,
    lookup_pantone_spot,
    match_nearest_pantone,
    normalize_pantone_name,
    resolve_spot_swatch_color,
)


# ---------------------------------------------------------------------------
# Normalisation + alternate-key matching.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("Pantone 485 C", "PANTONE 485 C"),
        ("PANTONE  485C", "PANTONE 485C"),
        ("  pantone  485 c", "PANTONE 485 C"),
        ("pantone reflex blue c", "PANTONE REFLEX BLUE C"),
        ("PMS 485", "PANTONE 485"),
        ("PMS 485 C", "PANTONE 485 C"),
        ("p.m.s. 485 c", "PANTONE 485 C"),
    ],
)
def test_normalize_pantone_name(input_name: str, expected: str) -> None:
    assert normalize_pantone_name(input_name) == expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("PANTONE 485 C", "PANTONE 485C"),
        ("PANTONE 485C", "PANTONE 485 C"),
        ("PANTONE Reflex Blue C", "PANTONE Reflex BlueC"),
        ("ECM Magenta", None),
    ],
)
def test_alternate_pantone_key(key: str, expected: str | None) -> None:
    assert alternate_pantone_key(key) == expected


# ---------------------------------------------------------------------------
# Colour math.
# ---------------------------------------------------------------------------


def test_lab_to_srgb_white_is_clamped_white() -> None:
    rgb = lab_d50_to_srgb((100.0, 0.0, 0.0))
    assert rgb == (255, 255, 255)


def test_lab_to_srgb_red_is_red_dominant() -> None:
    rgb = lab_d50_to_srgb((53.24, 80.09, 67.20))  # sRGB pure red Lab D50 approx.
    assert rgb[0] >= 250
    assert rgb[1] <= 30
    assert rgb[2] <= 30


def test_cmyk_to_srgb_naive_handles_percent_and_unit() -> None:
    rgb_percent = cmyk_to_srgb_naive((0.0, 100.0, 100.0, 0.0))
    rgb_unit = cmyk_to_srgb_naive((0.0, 1.0, 1.0, 0.0))
    assert rgb_percent == rgb_unit


def test_delta_e_76_is_zero_for_identical() -> None:
    assert delta_e_76((50.0, 0.0, 0.0), (50.0, 0.0, 0.0)) == 0.0


def test_delta_e_2000_known_value() -> None:
    # Sharma reference pair #1 (Lab1=(50, 2.6772, -79.7751),
    # Lab2=(50, 0.0, -82.7485)) → ΔE2000 ≈ 2.0425.
    de = delta_e_2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485))
    assert math.isclose(de, 2.0425, abs_tol=0.01)


# ---------------------------------------------------------------------------
# Pantone lookup + curated.
# ---------------------------------------------------------------------------


def test_pantone_lookup_finds_485_c() -> None:
    entry = lookup_pantone_spot("PANTONE 485 C")
    assert entry is not None
    assert entry.name.upper() == "PANTONE 485 C"
    assert entry.lab is not None
    assert entry.cmyk_bridge is not None


def test_pantone_lookup_alternate_spacing() -> None:
    entry_with_space = lookup_pantone_spot("PANTONE 485 C")
    entry_no_space = lookup_pantone_spot("PANTONE 485C")
    assert entry_with_space is not None
    assert entry_no_space is not None
    assert entry_with_space.lab == entry_no_space.lab


def test_pantone_lookup_unknown_returns_none() -> None:
    entry = lookup_pantone_spot("PANTONE Definitely Not A Real Color")
    assert entry is None


def test_pantone_overrides_take_precedence() -> None:
    overrides = {"PANTONE 485 C": {"lab": [12.0, 34.0, 56.0]}}
    entry = lookup_pantone_spot("PANTONE 485 C", extra_overrides=overrides)
    assert entry is not None
    assert entry.lab == (12.0, 34.0, 56.0)


def test_curated_lookup_handles_role_names() -> None:
    cut = lookup_curated_spot("Cut ")
    assert cut is not None
    assert cut.rgb == (236, 0, 140)
    dieline = lookup_curated_spot("Customer DieLine layer")
    assert dieline is not None
    assert dieline.rgb == (148, 0, 211)


def test_curated_lookup_unknown_returns_none() -> None:
    assert lookup_curated_spot("RebrandX") is None


# ---------------------------------------------------------------------------
# Resolver precedence.
# ---------------------------------------------------------------------------


def test_resolver_uses_host_override_first() -> None:
    result = resolve_spot_swatch_color(
        "PANTONE 485 C",
        host_override=SpotInkOverride(rgb=(10, 20, 30)),
    )
    assert result.source == "host"
    assert result.rgb == (10, 20, 30)


def test_resolver_uses_codex_intent_when_no_host() -> None:
    result = resolve_spot_swatch_color(
        "PANTONE 485 C",
        codex_intent=CodexSpotIntent(rgb=(7, 7, 7)),
    )
    assert result.source == "codex"
    assert result.rgb == (7, 7, 7)


def test_resolver_falls_back_to_pantone_db() -> None:
    result = resolve_spot_swatch_color("PANTONE 485 C")
    assert result.source == "pantone"
    assert result.lab is not None
    assert result.pantone_name is not None


def test_resolver_falls_back_to_curated_when_pantone_misses() -> None:
    result = resolve_spot_swatch_color("Cut ")
    assert result.source == "curated"
    assert result.rgb == (236, 0, 140)


def test_resolver_hash_when_nothing_matches() -> None:
    result = resolve_spot_swatch_color("RebrandX")
    assert result.source == "hash"
    assert all(0 <= c <= 255 for c in result.rgb)


def test_resolver_with_codex_pantone_name() -> None:
    intent = CodexSpotIntent(pantone_name="PANTONE 485 C")
    result = resolve_spot_swatch_color("Brand-Spot-1", codex_intent=intent)
    assert result.source == "pantone"
    assert result.lab is not None


def test_match_nearest_pantone_lab() -> None:
    # Pantone 485 C is a strong red — its nearest neighbour to its own
    # Lab triplet must be itself.
    ref = load_pantone_reference()
    entry = lookup_pantone_spot("PANTONE 485 C")
    assert entry is not None and entry.lab is not None
    nearest = match_nearest_pantone(entry.lab, reference=ref)
    assert nearest is not None
    matched, de = nearest
    assert matched.name == entry.name
    assert math.isclose(de, 0.0, abs_tol=0.01)


def test_inkbook_default_subset_includes_formula_guide() -> None:
    book = load_inkbook()
    assert book["schema_version"] == COLOR_SCHEMA_VERSION
    assert book["manifest"]["included_libraries"] == [
        "Pantone Formula Guide Coated",
        "Pantone Formula Guide Uncoated",
    ]
    assert book["manifest"]["included_count"] > 1000
    assert isinstance(book["pantone"], list)
    assert isinstance(book["curated"], list)
    assert any(item["tokens"] == ["dieline", "die-line", "die line", "die cut", "diecut"] for item in book["curated"])


def test_inkbook_full_subset() -> None:
    book = load_inkbook(libraries=["*"])
    assert book["manifest"]["included_count"] >= 20000


# ---------------------------------------------------------------------------
# HTTP API.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_color_resolve_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/color/resolve",
        json={
            "name": "PANTONE 485 C",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == COLOR_SCHEMA_VERSION
    assert payload["source"] == "pantone"
    assert payload["lab"] is not None
    assert len(payload["rgb"]) == 3


def test_color_resolve_endpoint_with_host_override(client: TestClient) -> None:
    response = client.post(
        "/v1/color/resolve",
        json={
            "name": "PANTONE 485 C",
            "host_override": {"rgb": [1, 2, 3]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "host"
    assert payload["rgb"] == [1, 2, 3]


def test_color_match_pantone_lab_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/color/match-pantone",
        json={"lab": [50.0, 50.0, 30.0]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == COLOR_SCHEMA_VERSION
    assert "PANTONE" in payload["pantone_name"].upper()
    assert payload["delta_e"] >= 0


def test_color_match_pantone_rgb_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/color/match-pantone",
        json={"rgb": [255, 0, 0]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["delta_e"] >= 0


def test_color_match_pantone_requires_measurement(client: TestClient) -> None:
    response = client.post("/v1/color/match-pantone", json={})
    assert response.status_code == 400


def test_color_inkbook_endpoint(client: TestClient) -> None:
    response = client.get("/v1/color/inkbook")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == COLOR_SCHEMA_VERSION
    assert "manifest" in payload
    assert isinstance(payload["pantone"], list)


def test_color_inkbook_filtered(client: TestClient) -> None:
    response = client.get("/v1/color/inkbook?libraries=Pantone%20Formula%20Guide%20Coated")
    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["included_libraries"] == ["Pantone Formula Guide Coated"]
