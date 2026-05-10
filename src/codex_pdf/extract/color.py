"""Color space and output intent extraction (pikepdf fallback)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from codex_pdf.color.alt_space import alt_to_swatch, evaluate_function
from codex_pdf.color.color_math import CmykQuad, LabTriplet, RgbTriplet
from codex_pdf.extract.common import obj_id, pdf_name
from codex_pdf.models.v1 import CodexColorSpace, CodexOutputIntent, CodexSpotColorant


_DEVICE_FAMILY_COMPONENTS = {
    "DeviceCMYK": 4,
    "DeviceRGB": 3,
    "DeviceGray": 1,
    "Lab": 3,
    "CalRGB": 3,
    "CalGray": 1,
}


def _is_pdf_array(value: Any) -> bool:
    """Truthy for a PDF array — handles both Python ``list`` and ``pikepdf.Array``.

    The original code used ``isinstance(value, list)`` which silently
    skipped pikepdf Arrays, so Separation/DeviceN colorants never
    populated their alt-space intent in the deployed pikepdf path.
    """
    if isinstance(value, list):
        return True
    if isinstance(value, (str, bytes)):
        return False
    type_name = type(value).__name__
    if type_name in {"Array", "_ObjectList"}:
        return True
    return hasattr(value, "__len__") and hasattr(value, "__getitem__") and hasattr(value, "__iter__")


def _alt_family_and_components(alt_value: Any) -> tuple[str | None, int | None]:
    """Resolve an alt-space PDF entry into (family-name, component-count)."""
    if alt_value is None:
        return None, None
    raw = pdf_name(alt_value) if str(alt_value).startswith("/") else None
    if raw and raw in _DEVICE_FAMILY_COMPONENTS:
        return raw, _DEVICE_FAMILY_COMPONENTS[raw]
    if _is_pdf_array(alt_value) and len(alt_value) >= 2:
        first = pdf_name(alt_value[0])
        if first == "ICCBased":
            stream = alt_value[1]
            n: int | None = None
            try:
                if hasattr(stream, "stream_dict"):
                    raw_n = stream.stream_dict.get("/N")
                    n = int(raw_n) if raw_n is not None else None
                elif hasattr(stream, "get"):
                    raw_n = stream.get("/N")
                    n = int(raw_n) if raw_n is not None else None
            except (TypeError, ValueError):
                n = None
            return "ICCBased", n
        if first in _DEVICE_FAMILY_COMPONENTS:
            return first, _DEVICE_FAMILY_COMPONENTS[first]
    return None, None


def _evaluate_separation_intent(
    alt_value: Any, fn: Any
) -> tuple[RgbTriplet | None, LabTriplet | None, CmykQuad | None]:
    """Evaluate a Separation tint transform at ``t=1.0`` and convert.

    Returns ``(rgb_u8, lab, cmyk_01)`` with each component populated
    only when the alt-space family supports it. Any failure returns
    ``(None, None, None)`` so the spot colorant simply lacks intent
    and the resolver falls through to the next swatch tier.
    """
    if fn is None:
        return None, None, None
    family, components = _alt_family_and_components(alt_value)
    if family is None:
        return None, None, None
    values = evaluate_function(fn, 1.0)
    if values is None:
        return None, None, None
    return alt_to_swatch(values, family, icc_components=components)


def _alt_id(alt_value: Any) -> str | None:
    """Stable identifier for the alt-space entry — Name or family tag."""
    if alt_value is None:
        return None
    raw = pdf_name(alt_value) if str(alt_value).startswith("/") else None
    if raw:
        return raw
    if _is_pdf_array(alt_value) and len(alt_value) >= 1:
        first = pdf_name(alt_value[0])
        if first:
            return first
    return None


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

        if _is_pdf_array(value) and len(value) > 0:
            first = pdf_name(value[0]) or "ICCBased"
            if first == "Separation":
                spot_name = pdf_name(value[1]) if len(value) > 1 else "Unknown"
                alt_value = value[2] if len(value) > 2 else None
                alt = _alt_id(alt_value)
                fn = value[3] if len(value) > 3 else None
                rgb, lab, cmyk = _evaluate_separation_intent(alt_value, fn)
                return CodexColorSpace(
                    id=cs_id,
                    family="Separation",
                    canonical={"raw": [str(v) for v in value]},
                    alternate_space_id=alt,
                    spot_colorants=[
                        CodexSpotColorant(
                            name=spot_name or "Unknown",
                            alternate_space_id=alt,
                            rgb=rgb,
                            lab=lab,
                            cmyk=cmyk,
                        )
                    ],
                )
            if first == "DeviceN":
                names = value[1] if len(value) > 1 else []
                alt_value = value[2] if len(value) > 2 else None
                alt = _alt_id(alt_value)
                spots: list[CodexSpotColorant] = []
                if _is_pdf_array(names):
                    for n in names:
                        nm = pdf_name(n)
                        if nm and nm not in {"All", "None"}:
                            spots.append(CodexSpotColorant(name=nm, alternate_space_id=alt))
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
