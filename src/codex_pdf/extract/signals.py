"""Structured signal extraction for downstream lint analyzers."""

from __future__ import annotations

from io import BytesIO
from typing import Any


def extract_analysis_signals_pikepdf(pdf_bytes: bytes) -> dict[str, Any]:
    """Extract additive analyzer-oriented signals for codex consumers."""
    try:
        import pikepdf
    except Exception:
        return {}

    out: dict[str, Any] = {}
    try:
        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            out["spot_names"] = _collect_spot_names(pdf)
            out["layer_names"] = _collect_layer_names(pdf)
            for idx, page in enumerate(pdf.pages, start=1):
                page_signals = _extract_page_signals(page)
                if page_signals:
                    out[f"page_{idx}"] = page_signals
    except Exception:
        return {}
    return out


def _extract_page_signals(page: Any) -> dict[str, Any]:
    resources = page.get("/Resources") if hasattr(page, "get") else None
    cs_dict = resources.get("/ColorSpace") if resources and hasattr(resources, "get") else None
    props_dict = resources.get("/Properties") if resources and hasattr(resources, "get") else None
    extgstate_dict = resources.get("/ExtGState") if resources and hasattr(resources, "get") else None

    media_box = None
    with_media = page.get("/MediaBox") if hasattr(page, "get") else None
    if with_media is not None and hasattr(with_media, "__len__"):
        try:
            media_box = [float(with_media[0]), float(with_media[1]), float(with_media[2]), float(with_media[3])]
        except Exception:
            media_box = None

    try:
        import pikepdf

        instructions = pikepdf.parse_content_stream(page)
    except Exception:
        instructions = []

    content_ops: list[dict[str, Any]] = []
    for inst in instructions:
        try:
            op = str(getattr(inst, "operator", ""))
            operands = list(getattr(inst, "operands", []))
        except Exception:
            continue
        content_ops.append(
            {
                "op": op,
                "operands": [_normalise_operand(v) for v in operands],
            }
        )

    return {
        "media_box": media_box,
        "content_ops": content_ops,
        "cs_to_spot": _build_cs_to_spot(cs_dict),
        "prop_to_ocg_name": _build_prop_to_ocg_name(props_dict),
        "extgstate": _build_extgstate_map(extgstate_dict),
    }


def _normalise_operand(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("latin-1")
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple)):
        return [_normalise_operand(v) for v in value]
    if hasattr(value, "items"):
        out: dict[str, Any] = {}
        try:
            for key, item in value.items():
                out[str(key)] = _normalise_operand(item)
            return out
        except Exception:
            return str(value)
    if hasattr(value, "__iter__"):
        try:
            return [_normalise_operand(v) for v in value]
        except Exception:
            return str(value)
    return str(value)


def _build_prop_to_ocg_name(props_dict: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if props_dict is None:
        return out
    try:
        items = props_dict.items() if hasattr(props_dict, "items") else []
    except Exception:
        return out
    for key, value in items:
        try:
            res_name = str(key).lstrip("/")
            if not hasattr(value, "get"):
                continue
            ocg_type = value.get("/Type")
            if ocg_type is not None and str(ocg_type).lstrip("/") != "OCG":
                continue
            ocg_name = value.get("/Name")
            if ocg_name is not None:
                out[res_name] = str(ocg_name)
        except Exception:
            continue
    return out


def _build_cs_to_spot(cs_dict: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if cs_dict is None:
        return out
    try:
        items = cs_dict.items() if hasattr(cs_dict, "items") else []
    except Exception:
        return out
    for key, value in items:
        try:
            name = str(key).lstrip("/")
            if not hasattr(value, "__getitem__"):
                continue
            first = value[0] if len(value) > 0 else None
            if str(first).lstrip("/") != "Separation":
                continue
            spot = value[1] if len(value) > 1 else None
            if spot is not None:
                out[name] = str(spot).lstrip("/")
        except Exception:
            continue
    return out


def _build_extgstate_map(extgstate_dict: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if extgstate_dict is None:
        return out
    try:
        items = extgstate_dict.items() if hasattr(extgstate_dict, "items") else []
    except Exception:
        return out
    for key, value in items:
        try:
            gs_name = str(key).lstrip("/")
            if not hasattr(value, "get"):
                continue
            entry: dict[str, Any] = {}
            op_value = value.get("/OP")
            if op_value is not None:
                entry["OP"] = bool(op_value)
            bm_value = value.get("/BM")
            if bm_value is not None:
                entry["BM"] = _normalise_operand(bm_value)
            ca_value = value.get("/CA")
            if ca_value is not None:
                entry["CA"] = float(ca_value)
            ca_lower = value.get("/ca")
            if ca_lower is not None:
                entry["ca"] = float(ca_lower)
            if entry:
                out[gs_name] = entry
        except Exception:
            continue
    return out


def _collect_spot_names(pdf: Any) -> list[str]:
    try:
        import pikepdf
    except Exception:
        return []

    names: list[str] = []
    seen_ids: set[int] = set()

    def _is_array(obj: Any) -> bool:
        return isinstance(obj, (list, pikepdf.Array))

    def _is_dictlike(obj: Any) -> bool:
        return isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) or (
            hasattr(obj, "items") and not _is_array(obj)
        )

    def _collect(obj: Any, depth: int) -> None:
        if depth > 10:
            return
        try:
            obj_key = id(obj)
        except Exception:
            obj_key = 0
        if obj_key in seen_ids:
            return
        seen_ids.add(obj_key)

        if _is_array(obj):
            try:
                arr = [obj[i] for i in range(len(obj))]
            except Exception:
                arr = list(obj)
            if len(arr) >= 2:
                subtype = str(arr[0])
                if subtype in ("/Separation", "Separation"):
                    try:
                        names.append(str(arr[1]).lstrip("/"))
                    except Exception:
                        pass
                elif subtype in ("/DeviceN", "DeviceN"):
                    comp = arr[1]
                    if _is_array(comp):
                        for n in comp:
                            try:
                                names.append(str(n).lstrip("/"))
                            except Exception:
                                continue
            for item in arr:
                _collect(item, depth + 1)
            return

        if _is_dictlike(obj):
            try:
                for _, value in obj.items():
                    _collect(value, depth + 1)
            except Exception:
                pass

    def _walk_resources(res: Any, depth: int) -> None:
        if res is None or not hasattr(res, "get"):
            return
        try:
            cs = res.get("/ColorSpace")
        except Exception:
            cs = None
        if cs is not None:
            _collect(cs, depth)

        for child_key in ("/XObject", "/Pattern"):
            try:
                child = res.get(child_key)
            except Exception:
                continue
            if child is None or not hasattr(child, "items"):
                continue
            try:
                for _, ref in child.items():
                    if not hasattr(ref, "get"):
                        continue
                    inner = ref.get("/Resources")
                    if inner is not None and depth < 10:
                        _walk_resources(inner, depth + 1)
            except Exception:
                continue

    for page in pdf.pages:
        try:
            _walk_resources(page.get("/Resources"), 0)
        except Exception:
            continue

    try:
        for obj in pdf.objects:
            _collect(obj, 0)
    except Exception:
        pass

    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _collect_layer_names(pdf: Any) -> list[str]:
    out: list[str] = []
    try:
        root = pdf.Root
        ocprops = root.get("/OCProperties")
        if ocprops is None:
            return out
        ocgs = ocprops.get("/OCGs") or []
        for ocg in ocgs:
            try:
                name = ocg.get("/Name")
                if name is not None:
                    out.append(str(name))
            except Exception:
                continue
    except Exception:
        return []
    return out
