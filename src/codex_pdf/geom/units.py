"""PDF user-space unit conversions.

PDF 1.7 / ISO 32000 default user-space is 1/72 inch ("points"). A
page's ``UserUnit`` entry can scale this for very large or very small
artwork — codex stores raw point values everywhere and converts at
the edges.
"""

from __future__ import annotations

INCHES_PER_POINT = 1.0 / 72.0
MM_PER_INCH = 25.4


def pt_to_in(value: float) -> float:
    """Convert PDF points (1/72 inch) to inches."""
    return value * INCHES_PER_POINT


def in_to_pt(value: float) -> float:
    """Convert inches to PDF points."""
    return value / INCHES_PER_POINT


def pt_to_mm(value: float) -> float:
    """Convert PDF points to millimetres."""
    return value * INCHES_PER_POINT * MM_PER_INCH


def mm_to_pt(value: float) -> float:
    """Convert millimetres to PDF points."""
    return value / (INCHES_PER_POINT * MM_PER_INCH)


def user_units_to_pt(value: float, *, user_unit: float = 1.0) -> float:
    """Convert a value in page-local user units to PDF points.

    ``user_unit`` defaults to 1.0 (the spec's default). Pages with a
    ``/UserUnit`` override pass that through unchanged.
    """
    return value * user_unit


def pt_to_user_units(value: float, *, user_unit: float = 1.0) -> float:
    """Inverse of :func:`user_units_to_pt`."""
    if user_unit == 0:
        raise ValueError("user_unit must be non-zero")
    return value / user_unit
