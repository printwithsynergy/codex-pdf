"""Annotation extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.extract.common import safe_box
from codex_pdf.models.v1 import CodexAnnotation


def extract_annotations_fitz(doc: Any) -> list[CodexAnnotation]:
    annotations: list[CodexAnnotation] = []
    for page_num, page in enumerate(doc, start=1):
        try:
            annots = page.annots()
            if not annots:
                continue
            for ann in annots:
                rect = getattr(ann, "rect", None)
                bbox = safe_box(rect) if rect is not None else None
                subtype = getattr(ann, "type", None)
                subtype_name = str(subtype[1]) if isinstance(subtype, tuple) and len(subtype) > 1 else None
                contents = None
                info = getattr(ann, "info", {})
                if isinstance(info, dict):
                    contents = info.get("content")
                annotations.append(
                    CodexAnnotation(
                        annotation_id=f"p{page_num}-a{len(annotations)+1}",
                        subtype=subtype_name,
                        page_num=page_num,
                        rect=bbox,
                        contents=contents,
                        has_appearance_stream=False,
                    )
                )
        except Exception:
            continue
    return annotations
