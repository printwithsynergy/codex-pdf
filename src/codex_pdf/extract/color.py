"""Color space and output intent extraction (pikepdf fallback)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from codex_pdf.extract.common import obj_id, pdf_name
from codex_pdf.models.v1 import CodexColorSpace, CodexOutputIntent, CodexSpotColorant


def extract_color_space(value: Any, cs_id: str) -> CodexColorSpace | None:
    try:
        if str(value).startswith("/"):
            family = pdf_name(value)
            if family in {
                "DeviceGray",
                "DeviceRGB",
                "DeviceCMYK",
                "Pattern",
                "Lab",
                "CalRGB",
                "CalGray",
                "Indexed",
            }:
                return CodexColorSpace(id=cs_id, family=family, canonical={"raw": str(value)})

        if isinstance(value, list) and len(value) > 0:
            first = pdf_name(value[0]) or "ICCBased"
            if first == "Separation":
                spot_name = pdf_name(value[1]) if len(value) > 1 else "Unknown"
                alt = pdf_name(value[2]) if len(value) > 2 else None
                return CodexColorSpace(
                    id=cs_id,
                    family="Separation",
                    canonical={"raw": [str(v) for v in value]},
                    alternate_space_id=alt,
                    spot_colorants=[CodexSpotColorant(name=spot_name or "Unknown", alternate_space_id=alt)],
                )
            if first == "DeviceN":
                names = value[1] if len(value) > 1 else []
                alt = pdf_name(value[2]) if len(value) > 2 else None
                spots: list[CodexSpotColorant] = []
                if isinstance(names, list):
                    for n in names:
                        if pdf_name(n) and pdf_name(n) not in {"All", "None"}:
                            spots.append(CodexSpotColorant(name=pdf_name(n) or "Unknown", alternate_space_id=alt))
                return CodexColorSpace(
                    id=cs_id,
                    family="DeviceN",
                    canonical={"raw": [str(v) for v in value]},
                    alternate_space_id=alt,
                    spot_colorants=spots,
                )
            if first in {"ICCBased", "Lab", "CalRGB", "CalGray", "Indexed", "Pattern"}:
                return CodexColorSpace(id=cs_id, family=first, canonical={"raw": [str(v) for v in value]})
    except Exception:
        return None
    return None


def extract_color_world_pikepdf(pdf_bytes: bytes) -> tuple[list[CodexOutputIntent], list[CodexColorSpace]]:
    output_intents: list[CodexOutputIntent] = []
    color_spaces: list[CodexColorSpace] = []
    try:
        import pikepdf

        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            root = pdf.Root
            out_arr = root.get("/OutputIntents", [])
            for idx, oi in enumerate(out_arr):
                output_intents.append(
                    CodexOutputIntent(
                        subtype=pdf_name(oi.get("/S")) if hasattr(oi, "get") else None,
                        output_condition_identifier=str(oi.get("/OutputConditionIdentifier"))
                        if hasattr(oi, "get") and oi.get("/OutputConditionIdentifier") is not None
                        else None,
                        profile_id=obj_id(oi.get("/DestOutputProfile"), f"outputintent-{idx}")
                        if hasattr(oi, "get")
                        else None,
                    )
                )

            cs_seen: set[str] = set()
            for page in pdf.pages:
                resources = page.obj.get("/Resources", {}) if hasattr(page.obj, "get") else {}
                cs_dict = resources.get("/ColorSpace", {}) if hasattr(resources, "get") else {}
                if hasattr(cs_dict, "items"):
                    for cs_name, cs_val in cs_dict.items():
                        cs_id = str(cs_name)
                        if cs_id in cs_seen:
                            continue
                        cs_seen.add(cs_id)
                        cs = extract_color_space(cs_val, cs_id)
                        if cs is not None:
                            color_spaces.append(cs)
    except Exception:
        pass
    return output_intents, color_spaces
