"""Axis-aligned bounding box ``Box`` primitive.

PDF MediaBox / CropBox / TrimBox / BleedBox / ArtBox are all
axis-aligned rectangles in user-space points; trap zones, layer
bboxes, and imposition cells are too. ``Box`` is the working type for
all of those.

Operations are intentionally exact (no floating-point widening) so
hashes / equality on box payloads are stable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """Axis-aligned rectangle on the PDF user-space plane.

    ``x0``/``y0`` is the lower-left corner; ``x1``/``y1`` is the
    upper-right. Empty / inside-out boxes are tolerated by the
    ``empty`` predicate; intersect / union / difference operations
    canonicalise their inputs so callers don't have to.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_bounds(cls, x0: float, y0: float, x1: float, y1: float) -> "Box":
        return cls(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    @classmethod
    def from_pdf(cls, mediabox: list[float] | tuple[float, ...]) -> "Box":
        """Build from a PDF MediaBox-style ``[x0, y0, x1, y1]``."""
        if len(mediabox) != 4:
            raise ValueError(f"PDF rectangle must have 4 elements, got {len(mediabox)}")
        return cls.from_bounds(*[float(v) for v in mediabox])

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def to_list(self) -> list[float]:
        """Return a PDF-style ``[x0, y0, x1, y1]`` list."""
        return [self.x0, self.y0, self.x1, self.y1]

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains(self, other: "Box") -> bool:
        return (
            self.x0 <= other.x0
            and self.y0 <= other.y0
            and self.x1 >= other.x1
            and self.y1 >= other.y1
        )

    def intersect(self, other: "Box") -> "Box":
        """Return the rectangular intersection (possibly empty)."""
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x1 < x0 or y1 < y0:
            return Box(0.0, 0.0, 0.0, 0.0)
        return Box(x0, y0, x1, y1)

    def union(self, other: "Box") -> "Box":
        """Return the smallest box containing both rectangles."""
        if self.empty:
            return other
        if other.empty:
            return self
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def difference(self, other: "Box") -> tuple["Box", ...]:
        """Return up to four rectangles covering ``self - other``.

        Returns an empty tuple when ``other`` fully contains ``self``;
        a single-rectangle tuple when ``other`` is disjoint from
        ``self``; otherwise the four-corner decomposition (top, bottom,
        left, right) of the residue.
        """
        clipped = self.intersect(other)
        if clipped.empty:
            return (self,)
        if (
            clipped.x0 == self.x0
            and clipped.y0 == self.y0
            and clipped.x1 == self.x1
            and clipped.y1 == self.y1
        ):
            return ()
        out: list[Box] = []
        if clipped.y1 < self.y1:
            out.append(Box(self.x0, clipped.y1, self.x1, self.y1))
        if clipped.y0 > self.y0:
            out.append(Box(self.x0, self.y0, self.x1, clipped.y0))
        if clipped.x0 > self.x0:
            out.append(Box(self.x0, clipped.y0, clipped.x0, clipped.y1))
        if clipped.x1 < self.x1:
            out.append(Box(clipped.x1, clipped.y0, self.x1, clipped.y1))
        return tuple(out)

    def offset(self, *, dx: float = 0.0, dy: float = 0.0) -> "Box":
        """Translate the box by ``dx, dy`` (returns a new box)."""
        return Box(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def inset(self, amount: float) -> "Box":
        """Return the box shrunk uniformly by ``amount`` per side.

        Negative ``amount`` expands the box (useful for trap-spread
        previews). The result is canonicalised; over-shrinking past
        the centre returns an empty zero-area box.
        """
        x0 = self.x0 + amount
        y0 = self.y0 + amount
        x1 = self.x1 - amount
        y1 = self.y1 - amount
        if x1 < x0 or y1 < y0:
            return Box(0.0, 0.0, 0.0, 0.0)
        return Box(x0, y0, x1, y1)

    def rotate_90(self, n: int = 1) -> "Box":
        """Rotate by ``n`` quarter turns around the origin (0, 0).

        Useful when normalising PDF page rotation. The returned box is
        canonicalised (lower-left, upper-right) regardless of which
        quarter-turn was applied.
        """
        n = n % 4
        x0, y0, x1, y1 = self.x0, self.y0, self.x1, self.y1
        for _ in range(n):
            x0, y0, x1, y1 = -y1, x0, -y0, x1
        return Box.from_bounds(x0, y0, x1, y1)

    def is_close(self, other: "Box", *, abs_tol: float = 1e-6) -> bool:
        """Float-tolerant equality."""
        return (
            math.isclose(self.x0, other.x0, abs_tol=abs_tol)
            and math.isclose(self.y0, other.y0, abs_tol=abs_tol)
            and math.isclose(self.x1, other.x1, abs_tol=abs_tol)
            and math.isclose(self.y1, other.y1, abs_tol=abs_tol)
        )
