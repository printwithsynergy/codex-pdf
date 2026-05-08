"""Polygon path primitive with Clipper2-backed boolean operations.

``Path`` stores one or more closed polygon rings on the PDF user-space
plane. Boolean operations (intersection, union, difference) and
polygon offsets (used later for trap spreads / chokes) are routed
through :mod:`pyclipr` (the Python binding for Clipper2) when it's
installed — that's the highest-confidence option for general-polygon
clipping.

When pyclipr is unavailable the boolean operations fall back to
axis-aligned rectangle math so the simple "rectangle ∩ rectangle"
case (the most common imposition / trap-zone need) still works.
Other shapes raise a clear ``RuntimeError`` so callers know they need
the optional dependency.

Public surface intentionally kept JSON-friendly: paths are sequences
of ``(x, y)`` tuples; codex doesn't store path *commands* (move-to /
line-to / curve-to) here — that's a future expansion seam for the
Forge producer services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from codex_pdf.geom.box import Box

logger = logging.getLogger(__name__)

Point = tuple[float, float]
Polygon = list[Point]


try:  # pragma: no cover - import-time only
    import pyclipr  # type: ignore[import-not-found]

    HAS_PYCLIPR: bool = True
except ImportError:  # pragma: no cover - exercised on environments without C++ toolchain
    pyclipr = None  # type: ignore[assignment]
    HAS_PYCLIPR = False


_CLIPPER_SCALE = 1_000_000  # Clipper2 wants integer coordinates; 6 sig figs is plenty.


def _polygon_bbox(poly: Sequence[Point]) -> Box:
    if not poly:
        return Box(0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return Box.from_bounds(min(xs), min(ys), max(xs), max(ys))


def _polygon_is_axis_aligned_rect(poly: Sequence[Point]) -> Box | None:
    if len(poly) != 4:
        return None
    xs = sorted({round(p[0], 6) for p in poly})
    ys = sorted({round(p[1], 6) for p in poly})
    if len(xs) != 2 or len(ys) != 2:
        return None
    return Box.from_bounds(xs[0], ys[0], xs[1], ys[1])


@dataclass(frozen=True)
class Path:
    """One or more closed polygon rings.

    Codex doesn't validate winding direction here — Clipper2 handles
    that internally. Self-intersecting rings produce Clipper2's
    NonZero / EvenOdd disambiguation; codex picks NonZero (matching
    the PDF default fill rule).
    """

    rings: tuple[Polygon, ...] = ()

    @classmethod
    def from_box(cls, box: Box) -> "Path":
        return cls(
            rings=(
                [
                    (box.x0, box.y0),
                    (box.x1, box.y0),
                    (box.x1, box.y1),
                    (box.x0, box.y1),
                ],
            )
        )

    @classmethod
    def from_polygons(cls, polygons: Iterable[Iterable[Point]]) -> "Path":
        return cls(rings=tuple([list(poly) for poly in polygons]))

    def bbox(self) -> Box:
        if not self.rings:
            return Box(0.0, 0.0, 0.0, 0.0)
        result = _polygon_bbox(self.rings[0])
        for ring in self.rings[1:]:
            result = result.union(_polygon_bbox(ring))
        return result

    def to_json(self) -> list[list[list[float]]]:
        return [[[p[0], p[1]] for p in ring] for ring in self.rings]

    @classmethod
    def from_json(cls, raw: list[list[list[float]]] | list[list[Point]]) -> "Path":
        rings: list[Polygon] = []
        for ring in raw:
            poly: Polygon = []
            for p in ring:
                if len(p) != 2:
                    raise ValueError("Polygon points must have 2 coordinates")
                poly.append((float(p[0]), float(p[1])))
            rings.append(poly)
        return cls(rings=tuple(rings))


def _scale_polygon(poly: Sequence[Point]) -> list[list[int]]:
    return [[round(p[0] * _CLIPPER_SCALE), round(p[1] * _CLIPPER_SCALE)] for p in poly]


def _unscale_polygon(poly: Iterable[Iterable[float]]) -> Polygon:
    return [(float(p[0]) / _CLIPPER_SCALE, float(p[1]) / _CLIPPER_SCALE) for p in poly]


def _try_box_path(path: Path) -> Box | None:
    if len(path.rings) != 1:
        return None
    return _polygon_is_axis_aligned_rect(path.rings[0])


def _rectangle_only(op: str, *paths: Path) -> tuple[Box, ...] | None:
    rects = [_try_box_path(path) for path in paths]
    if any(r is None for r in rects):
        return None
    boxes = [r for r in rects if r is not None]
    if op == "intersect":
        result = boxes[0]
        for b in boxes[1:]:
            result = result.intersect(b)
        return (result,) if not result.empty else ()
    if op == "union":
        result = boxes[0]
        for b in boxes[1:]:
            result = result.union(b)
        return (result,)
    if op == "difference":
        if len(boxes) != 2:
            return None
        residue = boxes[0].difference(boxes[1])
        return tuple(b for b in residue if not b.empty)
    return None


def _clipper_op(op: str, subjects: Sequence[Path], clips: Sequence[Path]) -> Path:
    if not HAS_PYCLIPR:
        raise RuntimeError(
            "polygon boolean operations on non-rectangular paths require the optional 'pyclipr' "
            "dependency; install codex-pdf with the [geom] extra (`pip install codex-pdf[geom]`)."
        )
    pc = pyclipr.Clipper()  # type: ignore[union-attr]
    pc.scaleFactor = 1
    for subject in subjects:
        for ring in subject.rings:
            pc.addPaths([_scale_polygon(ring)], pyclipr.PathType.Subject, False)  # type: ignore[union-attr]
    for clip in clips:
        for ring in clip.rings:
            pc.addPaths([_scale_polygon(ring)], pyclipr.PathType.Clip, False)  # type: ignore[union-attr]
    op_map = {
        "intersect": pyclipr.ClipType.Intersection,  # type: ignore[union-attr]
        "union": pyclipr.ClipType.Union,  # type: ignore[union-attr]
        "difference": pyclipr.ClipType.Difference,  # type: ignore[union-attr]
    }
    fill = pyclipr.FillRule.NonZero  # type: ignore[union-attr]
    paths = pc.execute(op_map[op], fill)
    rings = [_unscale_polygon(p) for p in paths]
    return Path(rings=tuple(rings))


def polygon_intersect(*paths: Path) -> Path:
    """Boolean intersection of one or more paths.

    Falls back to rectangle math when every input is axis-aligned.
    """
    rects = _rectangle_only("intersect", *paths)
    if rects is not None:
        return Path.from_polygons([list(_polygon_corners(b)) for b in rects])
    return _clipper_op("intersect", subjects=[paths[0]], clips=list(paths[1:]))


def polygon_union(*paths: Path) -> Path:
    """Boolean union of one or more paths."""
    rects = _rectangle_only("union", *paths)
    if rects is not None:
        return Path.from_polygons([list(_polygon_corners(b)) for b in rects])
    return _clipper_op("union", subjects=list(paths), clips=[])


def polygon_difference(subject: Path, clip: Path) -> Path:
    """Boolean difference ``subject - clip``."""
    rects = _rectangle_only("difference", subject, clip)
    if rects is not None:
        return Path.from_polygons([list(_polygon_corners(b)) for b in rects])
    return _clipper_op("difference", subjects=[subject], clips=[clip])


def _polygon_corners(box: Box) -> tuple[Point, Point, Point, Point]:
    return (
        (box.x0, box.y0),
        (box.x1, box.y0),
        (box.x1, box.y1),
        (box.x0, box.y1),
    )
