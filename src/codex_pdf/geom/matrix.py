"""PDF affine transformation matrix.

PDF stores CTMs as 6-tuples ``[a b c d e f]`` representing the 3×3
affine

    | a  b  0 |
    | c  d  0 |
    | e  f  1 |

with the third column fixed at ``[0, 0, 1]``. Codex's ``Matrix``
keeps the 6-tuple representation but exposes mathematical operations
in the standard left-to-right "apply matrix" convention used by the
PDF imaging model: ``new_point = old_point · M``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from codex_pdf.geom.box import Box


@dataclass(frozen=True)
class Matrix:
    """Affine PDF CTM (3×3 with [0,0,1] third column)."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @classmethod
    def identity(cls) -> "Matrix":
        return cls(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @classmethod
    def translation(cls, tx: float, ty: float) -> "Matrix":
        return cls(1.0, 0.0, 0.0, 1.0, tx, ty)

    @classmethod
    def scaling(cls, sx: float, sy: float | None = None) -> "Matrix":
        sy = sx if sy is None else sy
        return cls(sx, 0.0, 0.0, sy, 0.0, 0.0)

    @classmethod
    def rotation(cls, degrees: float) -> "Matrix":
        rad = math.radians(degrees)
        cos = math.cos(rad)
        sin = math.sin(rad)
        return cls(cos, sin, -sin, cos, 0.0, 0.0)

    @classmethod
    def from_pdf(cls, six: list[float] | tuple[float, ...]) -> "Matrix":
        if len(six) != 6:
            raise ValueError(f"PDF matrix must have 6 elements, got {len(six)}")
        return cls(*[float(v) for v in six])

    def to_list(self) -> list[float]:
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    def apply_point(self, x: float, y: float) -> tuple[float, float]:
        """Apply this matrix to ``(x, y)`` (PDF convention: row × M)."""
        return (
            self.a * x + self.c * y + self.e,
            self.b * x + self.d * y + self.f,
        )

    def apply_box(self, box: Box) -> Box:
        """Apply to a Box, returning the AABB of the transformed corners."""
        corners = [
            self.apply_point(box.x0, box.y0),
            self.apply_point(box.x1, box.y0),
            self.apply_point(box.x1, box.y1),
            self.apply_point(box.x0, box.y1),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return Box.from_bounds(min(xs), min(ys), max(xs), max(ys))

    def multiply(self, other: "Matrix") -> "Matrix":
        """Return ``self · other`` (PDF concatenation order).

        With the PDF convention ``point · M``, concatenating two
        matrices ``M1 · M2`` means "first apply M1, then M2".
        """
        return Matrix(
            a=self.a * other.a + self.b * other.c,
            b=self.a * other.b + self.b * other.d,
            c=self.c * other.a + self.d * other.c,
            d=self.c * other.b + self.d * other.d,
            e=self.e * other.a + self.f * other.c + other.e,
            f=self.e * other.b + self.f * other.d + other.f,
        )

    def determinant(self) -> float:
        return self.a * self.d - self.b * self.c

    def is_affine_invertible(self, *, abs_tol: float = 1e-12) -> bool:
        return abs(self.determinant()) > abs_tol

    def invert(self) -> "Matrix":
        det = self.determinant()
        if abs(det) < 1e-12:
            raise ValueError("Matrix is singular and cannot be inverted")
        inv_det = 1.0 / det
        return Matrix(
            a=self.d * inv_det,
            b=-self.b * inv_det,
            c=-self.c * inv_det,
            d=self.a * inv_det,
            e=(self.c * self.f - self.d * self.e) * inv_det,
            f=(self.b * self.e - self.a * self.f) * inv_det,
        )

    def is_close(self, other: "Matrix", *, abs_tol: float = 1e-9) -> bool:
        return all(
            math.isclose(getattr(self, attr), getattr(other, attr), abs_tol=abs_tol)
            for attr in ("a", "b", "c", "d", "e", "f")
        )

    def is_area_preserving(self, *, abs_tol: float = 1e-6) -> bool:
        """True iff ``|det| ≈ 1`` (rotation, reflection, or translation).

        Rotations and reflections preserve area; pure translations do
        too. Scales other than ±1 do not. Useful while validating page
        rotation normalisation: a 90° rotation must be area-preserving.
        """
        return math.isclose(abs(self.determinant()), 1.0, abs_tol=abs_tol)
