"""Font extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import CodexFont


def _outline_type(subtype: str) -> str:
    if "TrueType" in subtype:
        return "TrueType"
    if "CFF" in subtype or "Type1C" in subtype:
        return "CFF"
    if "Type1" in subtype:
        return "Type1"
    if "Type3" in subtype:
        return "Type3"
    if "CID" in subtype:
        return "CID"
    return "unknown"


def extract_fonts_fitz(doc: Any) -> list[CodexFont]:
    fonts: list[CodexFont] = []
    for idx, page in enumerate(doc, start=1):
        try:
            for font in page.get_fonts(full=True):
                font_key = str(font[0]) if len(font) > 0 else f"page{idx}-font"
                base_name = str(font[3]) if len(font) > 3 else None
                subtype = str(font[2]) if len(font) > 2 else "unknown"
                existing = next((f for f in fonts if f.font_id == font_key), None)
                if existing is None:
                    embedded = "subset" if base_name and "+" in base_name else "unknown"
                    fonts.append(
                        CodexFont(
                            font_id=font_key,
                            base_name=base_name,
                            subtype=subtype,
                            outline_type=_outline_type(subtype),
                            embedded=embedded,  # best-effort from naming convention.
                            missing_glyphs_detected=False,
                            page_refs=[idx],
                        )
                    )
                elif idx not in existing.page_refs:
                    existing.page_refs.append(idx)
        except Exception:
            continue
    return fonts
