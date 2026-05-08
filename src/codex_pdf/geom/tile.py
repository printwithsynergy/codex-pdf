"""Imposition tile-grid layout helper.

The ``tile_grid`` function computes a step-and-repeat layout from a
sheet rectangle, a cell size, gutters, and a marks-zone reservation.
It returns JSON-ready cell rectangles in user-space points, suitable
for previewing imposition geometry without emitting PDF bytes.

This is consumed by the upcoming ``impose-pdf`` producer service in
the Forge family, but the maths is read-only / additive — codex
exposes it now so loupe / lint can preview tile layouts in marketing
demos without round-tripping through Forge.
"""

from __future__ import annotations

from dataclasses import dataclass

from codex_pdf.geom.box import Box


@dataclass(frozen=True)
class MarksZone:
    """Edge-based marks reservation (registration / colour bars / labels).

    All four offsets default to zero. Positive values reserve that
    much space along the corresponding edge — the tile grid's
    available area shrinks accordingly.
    """

    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0


@dataclass(frozen=True)
class TileGrid:
    """Tile-grid input parameters."""

    sheet: Box
    cell_width: float
    cell_height: float
    gutter_x: float = 0.0
    gutter_y: float = 0.0
    marks_zone: MarksZone = MarksZone()
    origin: str = "bottom-left"


@dataclass(frozen=True)
class TileResult:
    """Computed tile-grid layout."""

    sheet: Box
    cells: tuple[Box, ...]
    rows: int
    cols: int
    used: Box
    waste: Box
    cell_width: float
    cell_height: float
    gutter_x: float
    gutter_y: float
    marks_zone: MarksZone


def tile_grid(grid: TileGrid) -> TileResult:
    """Compute a step-and-repeat layout.

    Returns the inner usable area (after marks-zone), the list of
    cell rectangles row-major from the chosen origin, and the
    leftover "waste" rectangle (the residual sheet area not occupied
    by any cell or by gutters).

    Raises ``ValueError`` when the inputs are nonsensical (zero or
    negative cell size, sheet smaller than one cell after marks).
    """
    if grid.cell_width <= 0 or grid.cell_height <= 0:
        raise ValueError("cell dimensions must be positive")
    if grid.gutter_x < 0 or grid.gutter_y < 0:
        raise ValueError("gutters cannot be negative")
    if grid.origin not in {"bottom-left", "top-left"}:
        raise ValueError(f"origin must be 'bottom-left' or 'top-left', got {grid.origin!r}")

    sheet = grid.sheet
    inner = Box(
        x0=sheet.x0 + grid.marks_zone.left,
        y0=sheet.y0 + grid.marks_zone.bottom,
        x1=sheet.x1 - grid.marks_zone.right,
        y1=sheet.y1 - grid.marks_zone.top,
    )
    if inner.empty:
        raise ValueError("marks zone consumes the entire sheet")

    available_w = inner.width
    available_h = inner.height

    cols = 0
    advance_x = grid.cell_width
    while True:
        cumulative = (cols + 1) * advance_x + cols * grid.gutter_x
        if cumulative > available_w + 1e-6:
            break
        cols += 1
        if cols >= 1024:  # safety: avoid runaway loops on tiny cells
            break

    rows = 0
    while True:
        cumulative = (rows + 1) * grid.cell_height + rows * grid.gutter_y
        if cumulative > available_h + 1e-6:
            break
        rows += 1
        if rows >= 1024:
            break

    if cols < 1 or rows < 1:
        raise ValueError("sheet is too small for even one cell after marks/gutter reservation")

    used_w = cols * grid.cell_width + max(0, cols - 1) * grid.gutter_x
    used_h = rows * grid.cell_height + max(0, rows - 1) * grid.gutter_y

    cells: list[Box] = []
    for row in range(rows):
        for col in range(cols):
            x0 = inner.x0 + col * (grid.cell_width + grid.gutter_x)
            if grid.origin == "bottom-left":
                y0 = inner.y0 + row * (grid.cell_height + grid.gutter_y)
            else:
                y0 = inner.y1 - (row + 1) * grid.cell_height - row * grid.gutter_y
            cells.append(
                Box.from_bounds(
                    x0,
                    y0,
                    x0 + grid.cell_width,
                    y0 + grid.cell_height,
                )
            )

    used = Box.from_bounds(
        inner.x0,
        inner.y0 if grid.origin == "bottom-left" else inner.y1 - used_h,
        inner.x0 + used_w,
        (inner.y0 + used_h) if grid.origin == "bottom-left" else inner.y1,
    )
    waste = Box(
        sheet.x0,
        sheet.y0,
        sheet.x1,
        sheet.y1,
    )
    return TileResult(
        sheet=sheet,
        cells=tuple(cells),
        rows=rows,
        cols=cols,
        used=used,
        waste=waste,
        cell_width=grid.cell_width,
        cell_height=grid.cell_height,
        gutter_x=grid.gutter_x,
        gutter_y=grid.gutter_y,
        marks_zone=grid.marks_zone,
    )
