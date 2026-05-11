"""Tests for the codex_pdf.geom package + /v1/geom/* endpoints."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from codex_pdf.api.main import app
from codex_pdf.geom import (
    GEOM_SCHEMA_VERSION,
    HAS_PYCLIPR,
    Box,
    MarksZone,
    Matrix,
    Path,
    TileGrid,
    in_to_pt,
    mm_to_pt,
    polygon_difference,
    polygon_intersect,
    polygon_offset,
    polygon_union,
    pt_to_in,
    pt_to_mm,
    tile_grid,
)


# ---------------------------------------------------------------------------
# Box arithmetic.
# ---------------------------------------------------------------------------


def test_box_from_pdf_canonicalises_inputs() -> None:
    box = Box.from_pdf([612.0, 792.0, 0.0, 0.0])
    assert (box.x0, box.y0, box.x1, box.y1) == (0.0, 0.0, 612.0, 792.0)


def test_box_intersect_overlapping() -> None:
    a = Box(0.0, 0.0, 100.0, 100.0)
    b = Box(50.0, 50.0, 150.0, 150.0)
    out = a.intersect(b)
    assert out == Box(50.0, 50.0, 100.0, 100.0)


def test_box_intersect_disjoint_returns_empty() -> None:
    a = Box(0.0, 0.0, 10.0, 10.0)
    b = Box(20.0, 20.0, 30.0, 30.0)
    out = a.intersect(b)
    assert out.empty


def test_box_difference_inner_hole() -> None:
    sheet = Box(0.0, 0.0, 100.0, 100.0)
    cell = Box(10.0, 10.0, 90.0, 90.0)
    residue = sheet.difference(cell)
    # Top, bottom, left, right strips.
    assert len(residue) == 4
    total_area = sum(r.area for r in residue) + cell.area
    assert math.isclose(total_area, sheet.area, abs_tol=1e-6)


def test_box_inset_canonicalises_overshrink() -> None:
    box = Box(0.0, 0.0, 10.0, 10.0)
    assert box.inset(20.0).empty


def test_box_rotate_quarter_turn_preserves_area() -> None:
    box = Box(10.0, 20.0, 30.0, 50.0)
    rotated = box.rotate_90()
    assert math.isclose(rotated.area, box.area)


# ---------------------------------------------------------------------------
# Matrix.
# ---------------------------------------------------------------------------


def test_matrix_identity_apply_point_no_change() -> None:
    m = Matrix.identity()
    assert m.apply_point(7.0, 13.0) == (7.0, 13.0)


def test_matrix_translation_then_scale_round_trip() -> None:
    box = Box(0.0, 0.0, 10.0, 20.0)
    m = Matrix.translation(5.0, 10.0).multiply(Matrix.scaling(2.0, 3.0))
    transformed = m.apply_box(box)
    assert math.isclose(transformed.area, 10.0 * 20.0 * 2.0 * 3.0)


def test_matrix_rotation_90_is_area_preserving() -> None:
    m = Matrix.rotation(90.0)
    assert m.is_area_preserving()


def test_matrix_invert_round_trip() -> None:
    m = Matrix.translation(15.0, -7.5).multiply(Matrix.scaling(2.5))
    inv = m.invert()
    composed = m.multiply(inv)
    assert composed.is_close(Matrix.identity(), abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Path boolean ops (rectangle fast path + Clipper2 fallback).
# ---------------------------------------------------------------------------


def test_polygon_intersect_rectangles() -> None:
    a = Path.from_box(Box(0.0, 0.0, 100.0, 100.0))
    b = Path.from_box(Box(50.0, 50.0, 150.0, 150.0))
    out = polygon_intersect(a, b)
    bbox = out.bbox()
    assert bbox.is_close(Box(50.0, 50.0, 100.0, 100.0), abs_tol=1e-6)


def test_polygon_difference_rectangles() -> None:
    a = Path.from_box(Box(0.0, 0.0, 100.0, 100.0))
    b = Path.from_box(Box(0.0, 0.0, 50.0, 50.0))
    out = polygon_difference(a, b)
    assert len(out.rings) > 0


def test_polygon_union_rectangles() -> None:
    a = Path.from_box(Box(0.0, 0.0, 50.0, 50.0))
    b = Path.from_box(Box(50.0, 0.0, 100.0, 50.0))
    out = polygon_union(a, b)
    bbox = out.bbox()
    assert bbox.is_close(Box(0.0, 0.0, 100.0, 50.0), abs_tol=1e-6)


@pytest.mark.skipif(not HAS_PYCLIPR, reason="pyclipr is required for non-rectangular boolean ops")
def test_polygon_intersect_triangles_via_clipper() -> None:
    triangle = Path.from_polygons([[(0.0, 0.0), (100.0, 0.0), (50.0, 100.0)]])
    rect = Path.from_box(Box(25.0, 0.0, 75.0, 50.0))
    out = polygon_intersect(triangle, rect)
    assert len(out.rings) >= 1


@pytest.mark.skipif(not HAS_PYCLIPR, reason="pyclipr is required for polygon offset")
def test_polygon_offset_triangle_spread_and_choke() -> None:
    """Triangle offset returns correctly-sized rings.

    Covers two bugs the pre-1.7.x codex had:

    1. ``ClipperOffset(miterLimit=...)`` raised ``TypeError`` under pyclipr
       0.1.8 (constructor dropped all kwargs). Triggered only on non-
       rectangular paths because rect inputs hit the fast-path bypass.
    2. The integer-scale factor used for the offset distance (1e3) didn't
       match the coord scale (1e6), so the effective offset was ×1000
       smaller than the caller asked for — a 10-pt request grew the bbox
       by ~0.01 pt. Assertions below check magnitude, not just direction,
       to catch a future re-introduction.
    """
    triangle = Path.from_polygons([[(0.0, 0.0), (100.0, 0.0), (50.0, 100.0)]])

    spread = polygon_offset(triangle, 10.0)
    assert len(spread.rings) == 1
    sb = spread.bbox()
    # A 10-pt spread should push the bbox at least ~9 pt outward on each
    # side; miter-join geometry on a sharp triangle apex may extend the y
    # max even further, so we only bound the *minimum* growth.
    assert sb.x0 <= -9.0 and sb.y0 <= -9.0, f"spread bbox not wide enough: {sb}"
    assert sb.x1 >= 109.0 and sb.y1 >= 109.0, f"spread bbox not tall enough: {sb}"

    choke = polygon_offset(triangle, -5.0)
    assert choke.rings
    cb = choke.bbox()
    # 5-pt choke should pull each side inward by ≥ ~4 pt.
    assert cb.x0 >= 4.0 and cb.y0 >= 4.0, f"choke bbox not inset enough: {cb}"
    assert cb.x1 <= 96.0 and cb.y1 <= 96.0, f"choke bbox not inset enough: {cb}"


# ---------------------------------------------------------------------------
# Tile grid.
# ---------------------------------------------------------------------------


def test_tile_grid_simple_2x2() -> None:
    grid = TileGrid(
        sheet=Box(0.0, 0.0, 200.0, 200.0),
        cell_width=80.0,
        cell_height=80.0,
        gutter_x=20.0,
        gutter_y=20.0,
    )
    out = tile_grid(grid)
    assert (out.rows, out.cols) == (2, 2)
    assert len(out.cells) == 4


def test_tile_grid_with_marks_zone_excludes_edge() -> None:
    grid = TileGrid(
        sheet=Box(0.0, 0.0, 200.0, 200.0),
        cell_width=50.0,
        cell_height=50.0,
        marks_zone=MarksZone(top=20.0, right=20.0, bottom=20.0, left=20.0),
    )
    out = tile_grid(grid)
    for cell in out.cells:
        assert cell.x0 >= 20.0
        assert cell.x1 <= 180.0
        assert cell.y0 >= 20.0
        assert cell.y1 <= 180.0


def test_tile_grid_origin_top_left_first_row_at_top() -> None:
    grid = TileGrid(
        sheet=Box(0.0, 0.0, 200.0, 200.0),
        cell_width=80.0,
        cell_height=80.0,
        gutter_x=20.0,
        gutter_y=20.0,
        origin="top-left",
    )
    out = tile_grid(grid)
    first_cell = out.cells[0]
    last_cell = out.cells[-1]
    # 2 rows × 80pt + gutter 20pt → top row top edge at sheet height,
    # bottom row top edge at sheet_top - cell - gutter - cell.
    assert first_cell.y1 == 200.0
    assert last_cell.y0 == 200.0 - 80.0 - 20.0 - 80.0


def test_tile_grid_rejects_oversized_cell() -> None:
    grid = TileGrid(
        sheet=Box(0.0, 0.0, 100.0, 100.0),
        cell_width=200.0,
        cell_height=200.0,
    )
    with pytest.raises(ValueError):
        tile_grid(grid)


# ---------------------------------------------------------------------------
# Unit conversions.
# ---------------------------------------------------------------------------


def test_unit_round_trips() -> None:
    assert math.isclose(in_to_pt(1.0), 72.0)
    assert math.isclose(pt_to_in(72.0), 1.0)
    assert math.isclose(mm_to_pt(25.4), 72.0, abs_tol=1e-6)
    assert math.isclose(pt_to_mm(72.0), 25.4, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# HTTP API.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_geom_tile_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/geom/tile",
        json={
            "sheet": {"x0": 0.0, "y0": 0.0, "x1": 200.0, "y1": 200.0},
            "cell_width": 80.0,
            "cell_height": 80.0,
            "gutter_x": 20.0,
            "gutter_y": 20.0,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == GEOM_SCHEMA_VERSION
    assert payload["rows"] == 2
    assert payload["cols"] == 2
    assert len(payload["cells"]) == 4


def test_geom_tile_endpoint_rejects_oversized_cell(client: TestClient) -> None:
    response = client.post(
        "/v1/geom/tile",
        json={
            "sheet": {"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
            "cell_width": 200.0,
            "cell_height": 200.0,
        },
    )
    assert response.status_code == 400


def test_geom_intersect_endpoint(client: TestClient) -> None:
    payload = {
        "subjects": [
            [[[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]],
        ],
        "clips": [
            [[[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]]],
        ],
    }
    response = client.post("/v1/geom/intersect", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == GEOM_SCHEMA_VERSION
    assert len(body["rings"]) >= 1
