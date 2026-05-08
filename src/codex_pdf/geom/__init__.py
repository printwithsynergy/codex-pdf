"""Codex geometry primitives — PDF user-space (1/72 inch) maths.

This package gives codex consumers (lint and loupe today; the future
producer services tomorrow) a single shared set of geometric
primitives for layer bbox math, CTM rebuild, imposition tile
previews, and trap-spread offsets. All numbers stay in PDF user
units (1/72 inch) by default; conversion helpers translate to/from
millimetres, inches, and points.

Public surface:

- :class:`Box` — axis-aligned bounding rectangle with intersect /
  union / difference / contains / area helpers.
- :class:`Matrix` — 3×2 affine PDF CTM with multiply / invert /
  translate / scale / rotate constructors and ``apply()`` for
  point + box transforms.
- :class:`Path` — ordered polygon ring(s) with boolean clipping
  backed by :mod:`pyclipr` when installed (highest-confidence
  Clipper2 implementation), with a documented exact-rectangle
  fallback for the simple case.
- :func:`tile_grid` — sheet imposition layout (cells + gutters +
  marks-zone reservation) returning JSON-ready cell rectangles.
- :func:`pt_to_mm`, :func:`mm_to_pt`, :func:`pt_to_in`,
  :func:`in_to_pt`, :func:`user_units_to_pt`, :func:`pt_to_user_units`
  — unit conversions.

Schema versioning: bump :data:`GEOM_SCHEMA_VERSION` independently of
the top-level codex-document schema. Endpoints carry the section
version on every response.
"""

from __future__ import annotations

from codex_pdf.geom.box import Box
from codex_pdf.geom.matrix import Matrix
from codex_pdf.geom.path import (
    HAS_PYCLIPR,
    Path,
    Point,
    Polygon,
    polygon_difference,
    polygon_intersect,
    polygon_union,
)
from codex_pdf.geom.tile import (
    MarksZone,
    TileGrid,
    TileResult,
    tile_grid,
)
from codex_pdf.geom.units import (
    in_to_pt,
    mm_to_pt,
    pt_to_in,
    pt_to_mm,
    pt_to_user_units,
    user_units_to_pt,
)

GEOM_SCHEMA_VERSION = "1.0.0"
"""Per-section schema version for the ``/v1/geom/*`` HTTP surface."""

__all__ = [
    "Box",
    "GEOM_SCHEMA_VERSION",
    "HAS_PYCLIPR",
    "MarksZone",
    "Matrix",
    "Path",
    "Point",
    "Polygon",
    "TileGrid",
    "TileResult",
    "in_to_pt",
    "mm_to_pt",
    "polygon_difference",
    "polygon_intersect",
    "polygon_union",
    "pt_to_in",
    "pt_to_mm",
    "pt_to_user_units",
    "tile_grid",
    "user_units_to_pt",
]
