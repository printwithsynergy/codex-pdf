"""Optional content group extraction."""

from __future__ import annotations

from io import BytesIO

from codex_pdf.extract.common import obj_id, pdf_name
from codex_pdf.models.v1 import CodexOCG


_STEP_HINTS = {
    "trap": "Trap",
    "white": "White",
    "varnish": "Varnish",
    "cut": "Cutting",
    "fold": "Folding",
    "dieline": "Dieline",
    "emboss": "Emboss",
    "bleed": "Bleed",
}


def _processing_step(name: str) -> str | None:
    lower = name.lower()
    for key, value in _STEP_HINTS.items():
        if key in lower:
            return value
    return None


def extract_ocgs_pikepdf(pdf_bytes: bytes) -> list[CodexOCG]:
    ocgs: list[CodexOCG] = []
    try:
        import pikepdf

        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            root = pdf.Root
            oc_props = root.get("/OCProperties", {})
            off_set: set[str] = set()
            default_cfg = oc_props.get("/D", {}) if hasattr(oc_props, "get") else {}
            off_list = default_cfg.get("/OFF", []) if hasattr(default_cfg, "get") else []
            for item in off_list:
                off_set.add(obj_id(item, str(item)))
            ocg_arr = oc_props.get("/OCGs", []) if hasattr(oc_props, "get") else []
            for idx, ocg in enumerate(ocg_arr):
                ocg_id = obj_id(ocg, f"ocg-{idx}")
                intent_raw = ocg.get("/Intent", []) if hasattr(ocg, "get") else []
                intents: list[str] = []
                if isinstance(intent_raw, list):
                    intents = [pdf_name(x) or str(x) for x in intent_raw]
                elif intent_raw is not None:
                    intents = [pdf_name(intent_raw) or str(intent_raw)]
                name = str(ocg.get("/Name")) if hasattr(ocg, "get") and ocg.get("/Name") else ocg_id
                ocgs.append(
                    CodexOCG(
                        ocg_id=ocg_id,
                        name=name,
                        default_visible=ocg_id not in off_set,
                        intent=intents,
                        iso19593_processing_step=_processing_step(name),
                    )
                )
    except Exception:
        pass
    return ocgs
