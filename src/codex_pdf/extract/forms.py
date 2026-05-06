"""Form XObject extraction."""

from __future__ import annotations

from io import BytesIO

from codex_pdf.extract.common import obj_id, pdf_name
from codex_pdf.models.v1 import CodexFormXObject


def extract_forms_pikepdf(pdf_bytes: bytes) -> list[CodexFormXObject]:
    form_xobjects: list[CodexFormXObject] = []
    try:
        import pikepdf

        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            seen: set[str] = set()
            for page_idx, page in enumerate(pdf.pages, start=1):
                resources = page.obj.get("/Resources", {}) if hasattr(page.obj, "get") else {}
                xobj_dict = resources.get("/XObject", {}) if hasattr(resources, "get") else {}
                if not hasattr(xobj_dict, "items"):
                    continue
                for x_name, x_obj in xobj_dict.items():
                    subtype = pdf_name(x_obj.get("/Subtype")) if hasattr(x_obj, "get") else None
                    if subtype != "Form":
                        continue
                    x_id = obj_id(x_obj, f"p{page_idx}-{x_name}")
                    if x_id in seen:
                        continue
                    seen.add(x_id)
                    child_refs: list[str] = []
                    child_res = x_obj.get("/Resources", {}) if hasattr(x_obj, "get") else {}
                    child_xobj = child_res.get("/XObject", {}) if hasattr(child_res, "get") else {}
                    if hasattr(child_xobj, "items"):
                        for child_name, child_obj in child_xobj.items():
                            child_refs.append(obj_id(child_obj, str(child_name)))
                    form_xobjects.append(
                        CodexFormXObject(
                            object_id=x_id,
                            parent_object_id=None,
                            resource_refs=child_refs or [str(x_name)],
                        )
                    )
    except Exception:
        pass
    return form_xobjects
