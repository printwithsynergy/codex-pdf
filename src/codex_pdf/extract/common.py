"""Shared extraction helpers."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import CodexBBox


def safe_box(rect: object) -> CodexBBox:
    x0 = float(getattr(rect, "x0", 0.0))
    y0 = float(getattr(rect, "y0", 0.0))
    x1 = float(getattr(rect, "x1", 0.0))
    y1 = float(getattr(rect, "y1", 0.0))
    return CodexBBox(x0=x0, y0=y0, x1=x1, y1=y1)


def pdf_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.startswith("/"):
        return text[1:]
    return text


def obj_id(value: Any, fallback: str) -> str:
    objgen = getattr(value, "objgen", None)
    if isinstance(objgen, tuple) and len(objgen) >= 1:
        return f"obj-{objgen[0]}"
    return fallback
