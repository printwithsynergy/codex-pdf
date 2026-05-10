"""PDF Function evaluation for Separation tint transforms (§7.10).

Used by the codex extractor to turn a ``Separation`` tint transform
into the canonical ``Lab`` / ``CMYK`` / sRGB triplets that
``CodexSpotColorant`` carries to viewers and rule engines. Without
this, a Separation like ``/Black Black /DeviceCMYK`` would lose its
ink intent and fall through the resolver to the deterministic-hash
swatch tier — green for "Black Black", which is exactly the bug
this module fixes.

Only the function types observed on real artwork are implemented:

- Type 2: exponential interpolation (≈95% of packaging spots)
- Type 3: stitching (delegates to subfunctions)

Type 0 (sampled) and Type 4 (PostScript calculator) currently fall
through — callers handle ``None`` by leaving the colorant intent
unset, so resolution gracefully degrades to the next swatch tier.
"""

from __future__ import annotations

from typing import Any

from codex_pdf.color.color_math import (
    CmykQuad,
    LabTriplet,
    RgbTriplet,
    cmyk_to_srgb_naive,
    lab_d50_to_srgb,
)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if hasattr(obj, "get"):
        try:
            v = obj.get(key, default)
        except Exception:
            return default
        return v if v is not None else default
    return default


def _floats(seq: Any) -> list[float] | None:
    if seq is None:
        return None
    try:
        return [float(v) for v in seq]
    except (TypeError, ValueError):
        return None


def _scalar(v: Any, default: float) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp01(v: float) -> float:
    if v != v:  # NaN
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def evaluate_function(fn: Any, x: float = 1.0) -> list[float] | None:
    """Evaluate a single-input PDF Function at ``x``.

    Returns the alternate-space output values, or ``None`` if the
    function type is unsupported or the dictionary is malformed.
    """
    raw_type = _get(fn, "/FunctionType")
    if raw_type is None:
        return None
    try:
        ftype = int(raw_type)
    except (TypeError, ValueError):
        return None
    if ftype == 2:
        return _eval_type2(fn, x)
    if ftype == 3:
        return _eval_type3(fn, x)
    return None


def _eval_type2(fn: Any, x: float) -> list[float] | None:
    domain = _floats(_get(fn, "/Domain"))
    if not domain or len(domain) < 2:
        return None
    x = max(domain[0], min(domain[1], x))
    n = _scalar(_get(fn, "/N"), 1.0)
    c0 = _floats(_get(fn, "/C0")) or [0.0]
    c1 = _floats(_get(fn, "/C1")) or [1.0]
    if len(c0) != len(c1):
        return None
    if n == 1.0:
        t = x
    else:
        try:
            t = pow(x, n) if x >= 0 else -pow(-x, n)
        except (ValueError, OverflowError):
            return None
    return [a + t * (b - a) for a, b in zip(c0, c1)]


def _eval_type3(fn: Any, x: float) -> list[float] | None:
    domain = _floats(_get(fn, "/Domain"))
    if not domain or len(domain) < 2:
        return None
    x = max(domain[0], min(domain[1], x))
    funcs_obj = _get(fn, "/Functions")
    if funcs_obj is None:
        return None
    try:
        funcs = list(funcs_obj)
    except TypeError:
        return None
    if not funcs:
        return None
    bounds = _floats(_get(fn, "/Bounds")) or []
    encode = _floats(_get(fn, "/Encode")) or []
    edges = [domain[0]] + list(bounds) + [domain[1]]
    idx = len(funcs) - 1
    for k in range(len(funcs)):
        if x <= edges[k + 1]:
            idx = k
            break
    a, b = edges[idx], edges[idx + 1]
    if 2 * idx + 1 < len(encode):
        e0, e1 = encode[2 * idx], encode[2 * idx + 1]
    else:
        e0, e1 = a, b
    x_sub = e0 if b == a else e0 + (x - a) * (e1 - e0) / (b - a)
    return evaluate_function(funcs[idx], x_sub)


def alt_to_swatch(
    values: list[float],
    alt_family: str | None,
    icc_components: int | None = None,
) -> tuple[tuple[float, float, float] | None, LabTriplet | None, CmykQuad | None]:
    """Convert evaluated alternate-space values to the canonical triplet.

    Returns ``(rgb_01, lab, cmyk_01)`` where ``rgb_01`` is 0-1 floats
    (matches the schema's ``CodexSpotColorant.rgb`` normalization,
    which expects 0-1 or 0-100 ranges, NOT 0-255 ints), ``lab`` is
    absolute CIE Lab, and ``cmyk_01`` is 0-1 fractions.

    ``icc_components`` is the ``/N`` of an ICCBased alternate when
    ``alt_family == "ICCBased"`` — used to pick a sane Device*
    interpretation (4=CMYK, 3=RGB, 1=Gray). Unmappable inputs return
    ``(None, None, None)`` and the caller falls through to the next
    swatch tier.
    """
    if not values:
        return None, None, None
    if alt_family == "DeviceCMYK" and len(values) == 4:
        cmyk = (
            _clamp01(values[0]),
            _clamp01(values[1]),
            _clamp01(values[2]),
            _clamp01(values[3]),
        )
        rgb_u8 = cmyk_to_srgb_naive(cmyk)
        rgb = (rgb_u8[0] / 255.0, rgb_u8[1] / 255.0, rgb_u8[2] / 255.0)
        return rgb, None, cmyk
    if alt_family == "DeviceRGB" and len(values) == 3:
        rgb = (_clamp01(values[0]), _clamp01(values[1]), _clamp01(values[2]))
        return rgb, None, None
    if alt_family == "DeviceGray" and len(values) == 1:
        v = _clamp01(values[0])
        return (v, v, v), None, None
    if alt_family == "Lab" and len(values) == 3:
        lab = (values[0], values[1], values[2])
        rgb_u8 = lab_d50_to_srgb(lab)
        rgb = (rgb_u8[0] / 255.0, rgb_u8[1] / 255.0, rgb_u8[2] / 255.0)
        return rgb, lab, None
    if alt_family == "CalRGB" and len(values) == 3:
        rgb = (_clamp01(values[0]), _clamp01(values[1]), _clamp01(values[2]))
        return rgb, None, None
    if alt_family == "ICCBased":
        n = icc_components or len(values)
        if n == 4 and len(values) == 4:
            return alt_to_swatch(values, "DeviceCMYK")
        if n == 3 and len(values) == 3:
            return alt_to_swatch(values, "DeviceRGB")
        if n == 1 and len(values) == 1:
            return alt_to_swatch(values, "DeviceGray")
    return None, None, None


__all__ = ["evaluate_function", "alt_to_swatch"]
