"""Pure-Python colour-space conversions for the spot-swatch resolver.

Mirrors ``loupe-pdf/host/spotColor/colorMath.ts`` byte-for-byte (same
matrices, same epsilon, same gamma piecewise) so resolver output is
identical between the in-process Python path and the in-browser TS
path. The routines here are dependency-free and run once per spot
ink, not per pixel — readability beats micro-optimisation.

Two paths matter for swatch display:

1. CIE Lab (D50, 2° observer) → sRGB triplet, via Bradford D50→D65
   chromatic adaptation. Pantone publishes Lab under D50 and the
   bundled reference holds D50 values.
2. CMYK → sRGB, naïve subtractive composite — only used as a final
   fallback when no Lab is available. Intentionally approximate; a
   real CMM pass would need an output ICC profile, which the codex
   colour endpoints do not own.

Plus the maths the resolver / nearest-Pantone search need:

- :func:`delta_e_76` — fast Lab Euclidean distance.
- :func:`delta_e_2000` — CIEDE2000 ΔE (matches lint-pdf's reference
  implementation; pure Python, no numpy required).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

LabTriplet = tuple[float, float, float]
RgbTriplet = tuple[int, int, int]
CmykQuad = tuple[float, float, float, float]


XYZ_D65_FROM_LINEAR_SRGB: tuple[tuple[float, float, float], ...] = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)

LINEAR_SRGB_FROM_XYZ_D65: tuple[tuple[float, float, float], ...] = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)

D50_TO_D65: tuple[tuple[float, float, float], ...] = (
    (0.9555766, -0.0230393, 0.0631636),
    (-0.0282895, 1.0099416, 0.0210077),
    (0.0122982, -0.0204830, 1.3299098),
)

D50_WHITE: tuple[float, float, float] = (0.9642, 1.0, 0.8249)
"""D50 reference white tristimulus (CIE 2° observer, X/Y/Z)."""


def _matmul3(
    triple: tuple[float, float, float],
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    a, b, c = triple
    return (
        matrix[0][0] * a + matrix[0][1] * b + matrix[0][2] * c,
        matrix[1][0] * a + matrix[1][1] * b + matrix[1][2] * c,
        matrix[2][0] * a + matrix[2][1] * b + matrix[2][2] * c,
    )


def _srgb_encode(linear: float) -> float:
    v = max(0.0, min(1.0, linear))
    if v <= 0.0031308:
        return 12.92 * v
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def srgb_decode(channel: float) -> float:
    """Inverse of the sRGB gamma encoding. ``channel`` is in [0, 1].

    Useful when a host hands codex a 0-255 sRGB triplet and we want to
    round-trip through XYZ without losing precision.
    """
    v = max(0.0, min(1.0, channel))
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def _clamp255(v: float) -> int:
    return max(0, min(255, round(v)))


def lab_d50_to_srgb(lab: LabTriplet) -> RgbTriplet:
    """Convert CIE Lab (D50, 2° observer) to an sRGB triplet on [0, 255].

    Out-of-gamut values are clamped per channel — display chips need a
    concrete colour, not ``None``. Identical to the lint-pdf and
    loupe-pdf TS implementations to within float-rounding.
    """
    L, a, b = lab
    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0
    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    fx_cubed = fx * fx * fx
    fz_cubed = fz * fz * fz
    xr = fx_cubed if fx_cubed > eps else (116.0 * fx - 16.0) / kappa
    yr = ((L + 16.0) / 116.0) ** 3 if L > kappa * eps else L / kappa
    zr = fz_cubed if fz_cubed > eps else (116.0 * fz - 16.0) / kappa

    x50 = xr * D50_WHITE[0]
    y50 = yr * D50_WHITE[1]
    z50 = zr * D50_WHITE[2]

    x65, y65, z65 = _matmul3((x50, y50, z50), D50_TO_D65)
    lr, lg, lb = _matmul3((x65, y65, z65), LINEAR_SRGB_FROM_XYZ_D65)

    return (
        _clamp255(_srgb_encode(lr) * 255),
        _clamp255(_srgb_encode(lg) * 255),
        _clamp255(_srgb_encode(lb) * 255),
    )


def cmyk_to_srgb_naive(cmyk: CmykQuad) -> RgbTriplet:
    """Convert CMYK (channel range auto-detected: 0-1 or 0-100) to sRGB.

    Naïve subtractive composite. Used only as a fallback inside the
    spot resolver when Lab is unavailable. Not ICC-correct; output is
    fine for display chips but should not drive press readouts.
    """
    c, m, y, k = cmyk
    is_percent = c > 1.0 or m > 1.0 or y > 1.0 or k > 1.0
    div = 100.0 if is_percent else 1.0
    cn = c / div
    mn = m / div
    yn = y / div
    kn = k / div
    r = (1.0 - cn) * (1.0 - kn)
    g = (1.0 - mn) * (1.0 - kn)
    b = (1.0 - yn) * (1.0 - kn)
    return (_clamp255(r * 255), _clamp255(g * 255), _clamp255(b * 255))


def delta_e_76(lab1: LabTriplet, lab2: LabTriplet) -> float:
    """Return CIE76 ΔE (Euclidean distance in Lab). O(1)."""
    dl = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    return math.sqrt(dl * dl + da * da + db * db)


def delta_e_2000(lab1: LabTriplet, lab2: LabTriplet) -> float:
    """Return CIEDE2000 ΔE between two Lab triplets.

    Pure-Python implementation following the Sharma et al. 2005
    canonical formulation. Result is rounded to 4 decimal places to
    keep test goldens stable across float-rounding edge cases.
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    avg_L = (L1 + L2) / 2.0
    C1 = math.sqrt(a1 * a1 + b1 * b1)
    C2 = math.sqrt(a2 * a2 + b2 * b2)
    avg_C = (C1 + C2) / 2.0

    G = 0.5 * (1.0 - math.sqrt((avg_C ** 7) / ((avg_C ** 7) + (25.0 ** 7))))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = math.sqrt(a1p * a1p + b1 * b1)
    C2p = math.sqrt(a2p * a2p + b2 * b2)
    avg_Cp = (C1p + C2p) / 2.0

    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0 if (a1p != 0 or b1 != 0) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0 if (a2p != 0 or b2 != 0) else 0.0

    if abs(h1p - h2p) > 180.0:
        avg_Hp = (h1p + h2p + 360.0) / 2.0
    else:
        avg_Hp = (h1p + h2p) / 2.0

    T = (
        1.0
        - 0.17 * math.cos(math.radians(avg_Hp - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * avg_Hp))
        + 0.32 * math.cos(math.radians(3.0 * avg_Hp + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * avg_Hp - 63.0))
    )

    if abs(h2p - h1p) <= 180.0:
        delta_hp = h2p - h1p
    elif h2p <= h1p:
        delta_hp = h2p - h1p + 360.0
    else:
        delta_hp = h2p - h1p - 360.0

    delta_Lp = L2 - L1
    delta_Cp = C2p - C1p
    delta_Hp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(delta_hp / 2.0))

    SL = 1.0 + (0.015 * (avg_L - 50.0) ** 2) / math.sqrt(20.0 + (avg_L - 50.0) ** 2)
    SC = 1.0 + 0.045 * avg_Cp
    SH = 1.0 + 0.015 * avg_Cp * T

    delta_theta = 30.0 * math.exp(-(((avg_Hp - 275.0) / 25.0) ** 2))
    RC = 2.0 * math.sqrt((avg_Cp ** 7) / ((avg_Cp ** 7) + (25.0 ** 7)))
    RT = -RC * math.sin(math.radians(2.0 * delta_theta))

    de = math.sqrt(
        (delta_Lp / SL) ** 2
        + (delta_Cp / SC) ** 2
        + (delta_Hp / SH) ** 2
        + RT * (delta_Cp / SC) * (delta_Hp / SH)
    )
    return round(de, 4)
