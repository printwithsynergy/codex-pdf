"""Codex separations / heatmap / point-sample renderer.

Ported from ``lint-pdf/src/lintpdf/reports/separation_renderer.py`` —
the canonical Ghostscript ``tiffsep`` decomposition path used for
per-channel separations, the TAC heatmap, the densitometer, and the
software composite tile that lint-pdf and loupe-pdf consume.

This module returns plain PNG bytes / dicts — no S3 caching — so it
is safe to call directly from the codex API and from
:mod:`codex_pdf.client`. Caching is layered above (in the API) when
``CODEX_REDIS_URL`` is configured.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any, TypedDict

import pikepdf
from PIL import Image

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


PROCESS_CHANNEL_COLORS: dict[str, tuple[int, int, int]] = {
    "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255),
    "Yellow": (255, 255, 0),
    "Black": (0, 0, 0),
}

PROCESS_CHANNEL_ORDER = ["Cyan", "Magenta", "Yellow", "Black"]


class TacRun(TypedDict):
    x0: float
    y0: float
    x1: float
    y1: float
    mean_tac: float
    limit: float
    exceeds: bool


class TacHeatmap(TypedDict):
    png: bytes
    runs: list[TacRun]


def _safe_channel_slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _run_tiffsep(pdf_bytes: bytes, page_num: int, dpi: int, tmpdir: str) -> str:
    pdf_path = os.path.join(tmpdir, "input.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    output_base = os.path.join(tmpdir, "sep")
    cmd = [
        "gs",
        "-q",
        "-sDEVICE=tiffsep",
        "-dNOPAUSE",
        "-dBATCH",
        f"-r{dpi}",
        f"-dFirstPage={page_num}",
        f"-dLastPage={page_num}",
        f"-sOutputFile={output_base}%d.tif",
        pdf_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        logger.error("Ghostscript tiffsep failed: %s", stderr)
        raise RuntimeError(f"Ghostscript separation failed: {stderr[:500]}")
    return output_base


def _pct_array_from_tiff(tif_path: str) -> "np.ndarray":
    import numpy as np

    img = Image.open(tif_path).convert("L")
    arr = 255 - np.array(img, dtype=np.float32)
    return arr * (100.0 / 255.0)


def _pct_array_to_png_bytes(arr: "np.ndarray") -> bytes:
    import numpy as np

    inked = np.clip(arr, 0.0, 100.0) * (255.0 / 100.0)
    gray = (255.0 - inked).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(gray, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _find_channel_tif(tmpdir: str, channel: str, _output_base: str) -> str | None:
    files = sorted(os.listdir(tmpdir))
    channel_lower = channel.lower()
    for f in files:
        if not f.endswith(".tif"):
            continue
        fpath = os.path.join(tmpdir, f)
        fname_lower = f.lower()
        if f"({channel_lower})" in fname_lower:
            return fpath
        if f".{channel_lower}.tif" in fname_lower:
            return fpath
        if channel_lower.replace(" ", "_") in fname_lower:
            return fpath
    if channel in PROCESS_CHANNEL_ORDER:
        for f in files:
            if not f.endswith(".tif"):
                continue
            fpath = os.path.join(tmpdir, f)
            if channel.lower() in f.lower():
                return fpath
    return None


# ---------------------------------------------------------------------------
# /Resources color-space scan (drives list_separations).
# ---------------------------------------------------------------------------

_MAX_XOBJECT_DEPTH = 12


def _safe_get(obj: object, key: str) -> object | None:
    if obj is None or not hasattr(obj, "get"):
        return None
    try:
        return obj.get(key)
    except Exception:
        return None


def _extract_spot_from_cs(
    cs_value: object,
    seen_names: set[str],
    channels: list[dict],
    families: set[str],
) -> None:
    try:
        if not hasattr(cs_value, "__iter__") or isinstance(cs_value, (str, bytes)):
            cs_type = str(cs_value)
            if cs_type == "/DeviceCMYK":
                families.add("cmyk")
            elif cs_type == "/DeviceRGB":
                families.add("rgb")
            elif cs_type == "/DeviceGray":
                families.add("gray")
            return

        cs_array = list(cs_value)
        if not cs_array:
            return

        cs_type = str(cs_array[0])

        if cs_type == "/DeviceCMYK":
            families.add("cmyk")
        elif cs_type in ("/DeviceRGB", "/CalRGB"):
            families.add("rgb")
        elif cs_type in ("/DeviceGray", "/CalGray"):
            families.add("gray")
        elif cs_type == "/ICCBased":
            if len(cs_array) >= 2:
                stream = cs_array[1]
                n = 0
                try:
                    n = int(stream.get("/N", 0)) if hasattr(stream, "get") else 0
                except Exception:
                    n = 0
                if n == 4:
                    families.add("cmyk")
                elif n == 3:
                    families.add("rgb")
                elif n == 1:
                    families.add("gray")
        elif cs_type == "/Lab":
            families.add("rgb")
        elif cs_type == "/Indexed":
            if len(cs_array) >= 2:
                _extract_spot_from_cs(cs_array[1], seen_names, channels, families)
        elif cs_type == "/Pattern":
            if len(cs_array) >= 2:
                _extract_spot_from_cs(cs_array[1], seen_names, channels, families)
        elif cs_type == "/Separation":
            if len(cs_array) >= 2:
                name = str(cs_array[1]).lstrip("/")
                if name not in seen_names and name not in ("All", "None"):
                    seen_names.add(name)
                    channels.append({"name": name, "type": "spot"})
        elif cs_type == "/DeviceN" and len(cs_array) >= 2:
            names_array = cs_array[1]
            for n_obj in names_array:
                name = str(n_obj).lstrip("/")
                if name in ("All", "None"):
                    continue
                if name in PROCESS_CHANNEL_COLORS:
                    families.add("cmyk")
                    continue
                if name not in seen_names:
                    seen_names.add(name)
                    channels.append({"name": name, "type": "spot"})
    except Exception:
        pass


def _scan_resources_dict(
    resources: object,
    seen_names: set[str],
    channels: list[dict],
    families: set[str],
    visited: set[int],
    depth: int,
) -> None:
    if depth > _MAX_XOBJECT_DEPTH or resources is None:
        return

    cs_dict = _safe_get(resources, "/ColorSpace")
    if cs_dict is not None and hasattr(cs_dict, "keys"):
        try:
            for _key, cs_value in dict(cs_dict).items():
                _extract_spot_from_cs(cs_value, seen_names, channels, families)
        except Exception:
            pass

    pattern_dict = _safe_get(resources, "/Pattern")
    if pattern_dict is not None:
        try:
            for _key, pat in dict(pattern_dict).items():
                if hasattr(pat, "get"):
                    pat_cs = pat.get("/Resources")
                    if pat_cs is not None:
                        _scan_resources_dict(
                            pat_cs, seen_names, channels, families, visited, depth + 1
                        )
        except Exception:
            pass

    shading_dict = _safe_get(resources, "/Shading")
    if shading_dict is not None:
        try:
            for _key, sh in dict(shading_dict).items():
                if hasattr(sh, "get"):
                    sh_cs = sh.get("/ColorSpace")
                    if sh_cs is not None:
                        _extract_spot_from_cs(sh_cs, seen_names, channels, families)
        except Exception:
            pass

    xobjects = _safe_get(resources, "/XObject")
    if xobjects is None:
        return
    try:
        for _key, xobj in dict(xobjects).items():
            if not hasattr(xobj, "get"):
                continue
            obj_id = id(xobj)
            if obj_id in visited:
                continue
            visited.add(obj_id)

            subtype = xobj.get("/Subtype")
            subtype_str = str(subtype) if subtype is not None else ""

            if subtype_str == "/Image":
                img_cs = xobj.get("/ColorSpace")
                if img_cs is not None:
                    _extract_spot_from_cs(img_cs, seen_names, channels, families)
                continue

            sub_resources = xobj.get("/Resources")
            if sub_resources is not None:
                _scan_resources_dict(
                    sub_resources, seen_names, channels, families, visited, depth + 1
                )
    except Exception:
        pass


def _scan_page_colorspaces(
    page: pikepdf.Page,
    seen_names: set[str],
    channels: list[dict],
    families: set[str],
) -> None:
    resources = page.get("/Resources")
    if resources is None:
        return
    _scan_resources_dict(resources, seen_names, channels, families, visited=set(), depth=0)


def list_separations(pdf_bytes: bytes) -> list[dict]:
    """Return ink channels actually present in the PDF.

    Output shape: ``[{"name": "Cyan", "type": "process"}, ...]``.
    Process families come first, spots in first-seen order.
    """
    channels: list[dict] = []
    seen_names: set[str] = set()
    families: set[str] = set()

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            _scan_page_colorspaces(page, seen_names, channels, families)

    prefix: list[dict] = []
    if "cmyk" in families:
        for name in PROCESS_CHANNEL_ORDER:
            prefix.append({"name": name, "type": "process"})
    if "rgb" in families:
        for name in ("Red", "Green", "Blue"):
            prefix.append({"name": name, "type": "rgb"})
    if "gray" in families and "cmyk" not in families and "rgb" not in families:
        prefix.append({"name": "Gray", "type": "gray"})

    return prefix + channels


# ---------------------------------------------------------------------------
# CMYK + spot channel rendering (no S3 cache here — caching belongs in the
# API layer keyed by sha256(pdf)+args).
# ---------------------------------------------------------------------------


def get_cmyk_channels(
    pdf_bytes: bytes,
    page_num: int,
    dpi: int,
) -> tuple[list["np.ndarray"], list[str]]:
    with tempfile.TemporaryDirectory(prefix="codex_cmyk_") as tmpdir:
        output_base = _run_tiffsep(pdf_bytes, page_num, dpi, tmpdir)
        arrays: list[np.ndarray] = []
        for ch in PROCESS_CHANNEL_ORDER:
            tif = _find_channel_tif(tmpdir, ch, output_base)
            if tif is None:
                raise RuntimeError(f"CMYK channel '{ch}' not found in GS output")
            arrays.append(_pct_array_from_tiff(tif))
    return arrays, list(PROCESS_CHANNEL_ORDER)


def render_separation_channel(
    pdf_bytes: bytes,
    page_num: int,
    channel: str,
    dpi: int = 150,
) -> bytes:
    """Render a single channel (process or spot) as a grayscale PNG.

    Polarity matches Ghostscript ``tiffsep``: ``0 = full ink``,
    ``255 = no ink`` so consumers can sum / composite pixel-equally.
    """
    if channel in PROCESS_CHANNEL_ORDER:
        arrays, names = get_cmyk_channels(pdf_bytes, page_num, dpi)
        idx = names.index(channel)
        return _pct_array_to_png_bytes(arrays[idx])

    with tempfile.TemporaryDirectory(prefix="codex_sep_") as tmpdir:
        output_base = _run_tiffsep(pdf_bytes, page_num, dpi, tmpdir)
        channel_tif = _find_channel_tif(tmpdir, channel, output_base)
        if channel_tif is None:
            raise RuntimeError(
                f"Channel '{channel}' not found in Ghostscript output. "
                f"Available files: {os.listdir(tmpdir)}"
            )
        img = Image.open(channel_tif).convert("L")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def render_separations(
    pdf_bytes: bytes,
    page_num: int,
    *,
    dpi: int = 150,
) -> dict[str, Any]:
    """Render every separation present on ``page_num`` in one tiffsep run.

    Returns:
        ``{"channels": [{"name", "type", "png": <bytes>}, ...]}``
    """
    inventory = list_separations(pdf_bytes)
    out_channels: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="codex_seps_") as tmpdir:
        output_base = _run_tiffsep(pdf_bytes, page_num, dpi, tmpdir)
        for entry in inventory:
            if entry.get("type") not in ("process", "spot"):
                continue
            ch_name = entry["name"]
            tif = _find_channel_tif(tmpdir, ch_name, output_base)
            if tif is None:
                continue
            img = Image.open(tif).convert("L")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out_channels.append(
                {"name": ch_name, "type": entry["type"], "png": buf.getvalue()}
            )

    return {"channels": out_channels, "page_num": page_num, "dpi": dpi}


# ---------------------------------------------------------------------------
# TAC heatmap.
# ---------------------------------------------------------------------------


def _extract_text_bboxes(
    pdf_bytes: bytes, page_num: int
) -> list[tuple[float, float, float, float]]:
    # nosemgrep: use-defused-xml — output of our own pdftotext, not external input
    from xml.etree.ElementTree import fromstring

    with tempfile.TemporaryDirectory(prefix="codex_txt_") as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        html_path = os.path.join(tmpdir, "out.xhtml")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        cmd = [
            "pdftotext",
            "-bbox",
            "-f",
            str(page_num),
            "-l",
            str(page_num),
            pdf_path,
            html_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0 or not os.path.exists(html_path):
            return []

        try:
            with open(html_path, encoding="utf-8", errors="replace") as f:
                xml_body: str = f.read()
        except OSError:
            return []

    body_idx = xml_body.find("<html")
    if body_idx == -1:
        return []
    cleaned = xml_body[body_idx:]
    try:
        root = fromstring(cleaned)
    except Exception:
        return []

    words: list[tuple[float, float, float, float]] = []
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag.split("}")[-1] != "word":
            continue
        try:
            x0 = float(el.get("xMin") or 0)
            y0 = float(el.get("yMin") or 0)
            x1 = float(el.get("xMax") or 0)
            y1 = float(el.get("yMax") or 0)
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        words.append((x0, y0, x1, y1))

    if not words:
        return []

    words.sort(key=lambda b: (round(b[1], 1), b[0]))

    merged: list[list[float]] = []
    line_tol = 2.0
    for x0, y0, x1, y1 in words:
        height = max(1.0, y1 - y0)
        gap_tol = max(6.0, height * 0.75)
        if merged:
            prev = merged[-1]
            prev_height = max(1.0, prev[3] - prev[1])
            same_line = (
                abs(y0 - prev[1]) <= line_tol
                and abs(y1 - prev[3]) <= line_tol * 2
                and abs(height - prev_height) <= max(2.0, prev_height * 0.3)
            )
            gap_ok = x0 - prev[2] <= gap_tol
            if same_line and gap_ok:
                prev[2] = max(prev[2], x1)
                prev[3] = max(prev[3], y1)
                prev[1] = min(prev[1], y0)
                continue
        merged.append([x0, y0, x1, y1])

    if len(merged) > 400:
        merged = merged[:400]

    return [(b[0], b[1], b[2], b[3]) for b in merged]


def render_heatmap(
    pdf_bytes: bytes,
    page_num: int,
    *,
    dpi: int = 150,
    tac_limit: float = 300,
) -> TacHeatmap:
    """Generate a TAC heatmap PNG plus per-text-run TAC metadata."""
    import numpy as np

    cmyk_arrays, _ = get_cmyk_channels(pdf_bytes, page_num, dpi)
    tac = cmyk_arrays[0] + cmyk_arrays[1] + cmyk_arrays[2] + cmyk_arrays[3]
    height, width = tac.shape

    heatmap = np.zeros((height, width, 4), dtype=np.uint8)

    green_mask = (tac >= 1) & (tac < 250)
    heatmap[green_mask] = [0, 180, 0, 100]

    yellow_mask = (tac >= 250) & (tac < tac_limit)
    heatmap[yellow_mask] = [255, 200, 0, 150]

    red_mask = tac >= tac_limit
    heatmap[red_mask] = [255, 0, 0, 190]

    paper_mask = tac < 1
    heatmap[paper_mask] = [0, 0, 0, 0]

    heatmap_img = Image.fromarray(heatmap, mode="RGBA")

    try:
        text_bboxes = _extract_text_bboxes(pdf_bytes, page_num)
    except Exception:
        logger.warning("TAC heatmap: text-bbox extraction failed", exc_info=True)
        text_bboxes = []

    runs: list[TacRun] = []

    if text_bboxes:
        try:
            with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
                mb = pdf.pages[page_num - 1].get("/MediaBox")
                mb_vals = [float(v) for v in mb] if mb is not None else [0, 0, 612, 792]
        except Exception:
            mb_vals = [0.0, 0.0, float(width), float(height)]

        page_w_pt = mb_vals[2] - mb_vals[0] or 1.0
        page_h_pt = mb_vals[3] - mb_vals[1] or 1.0
        sx = width / page_w_pt
        sy = height / page_h_pt

        from PIL import ImageDraw

        draw = ImageDraw.Draw(heatmap_img)
        stroke = (220, 0, 0, 230)
        stroke_w = max(2, round(dpi / 72))

        for x0, y0, x1, y1 in text_bboxes:
            px_x0 = max(0, round(x0 * sx))
            px_y0 = max(0, round(y0 * sy))
            px_x1 = min(width, round(x1 * sx))
            px_y1 = min(height, round(y1 * sy))
            if px_x1 - px_x0 < 2 or px_y1 - px_y0 < 2:
                continue

            patch = tac[px_y0:px_y1, px_x0:px_x1]
            if patch.size == 0:
                continue
            mean_tac = float(patch.mean())
            exceeds = mean_tac >= tac_limit

            runs.append(
                TacRun(
                    x0=float(x0),
                    y0=float(y0),
                    x1=float(x1),
                    y1=float(y1),
                    mean_tac=round(mean_tac, 2),
                    limit=float(tac_limit),
                    exceeds=exceeds,
                )
            )

            if not exceeds:
                continue

            draw.rectangle(
                (px_x0, px_y0, px_x1 - 1, px_y1 - 1),
                outline=stroke,
                width=stroke_w,
            )

    buf = io.BytesIO()
    heatmap_img.save(buf, format="PNG")
    return TacHeatmap(png=buf.getvalue(), runs=runs)


# ---------------------------------------------------------------------------
# Densitometer + color samples.
# ---------------------------------------------------------------------------


def sample_density(
    pdf_bytes: bytes,
    page_num: int,
    *,
    x: float,
    y: float,
    page_w: float,
    page_h: float,
    dpi: int = 300,
    tac_limit: float = 300,
) -> dict[str, object]:
    """Sample per-channel ink coverage + TAC at a PDF-space point.

    Coordinates ``x``/``y`` are in PDF points with origin lower-left.
    """
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="codex_dens_") as tmpdir:
        output_base = _run_tiffsep(pdf_bytes, page_num, dpi, tmpdir)
        channel_files: list[tuple[str, str]] = []
        for ch_name in PROCESS_CHANNEL_ORDER:
            ch_tif = _find_channel_tif(tmpdir, ch_name, output_base)
            if ch_tif is not None:
                channel_files.append((ch_name, ch_tif))

        process_lower = {n.lower() for n in PROCESS_CHANNEL_ORDER}
        already = {ch[0].lower() for ch in channel_files}
        for name in sorted(os.listdir(tmpdir)):
            if not name.endswith(".tif"):
                continue
            if "(" not in name or ")" not in name:
                continue
            spot = name[name.index("(") + 1 : name.rindex(")")]
            if not spot or spot.lower() in process_lower or spot.lower() in already:
                continue
            channel_files.append((spot, os.path.join(tmpdir, name)))
            already.add(spot.lower())

        if not channel_files:
            raise RuntimeError("No separation channels produced for this page")

        first_arr = _pct_array_from_tiff(channel_files[0][1])
        img_h, img_w = first_arr.shape
        scale_x = img_w / page_w if page_w else 1.0
        scale_y = img_h / page_h if page_h else 1.0
        px_x = round(x * scale_x)
        px_y = round(img_h - y * scale_y)
        px_x = max(0, min(px_x, img_w - 1))
        px_y = max(0, min(px_y, img_h - 1))

        def _sample_patch(arr: np.ndarray) -> float:
            x0 = max(0, px_x - 1)
            x1 = min(img_w, px_x + 2)
            y0 = max(0, px_y - 1)
            y1 = min(img_h, px_y + 2)
            patch = arr[y0:y1, x0:x1]
            if patch.size == 0:
                return 0.0
            return max(0.0, min(100.0, float(patch.mean())))

        channel_entries: list[dict[str, Any]] = []
        for ch_name, tif_path in channel_files:
            arr = first_arr if tif_path == channel_files[0][1] else _pct_array_from_tiff(tif_path)
            channel_entries.append(
                {"name": ch_name, "percent": round(_sample_patch(arr), 2)}
            )

    tac = round(sum(float(ch["percent"]) for ch in channel_entries), 2)

    return {
        "x": x,
        "y": y,
        "dpi": dpi,
        "channels": channel_entries,
        "tac": tac,
        "tac_limit": tac_limit,
        "limit_exceeded": tac > tac_limit,
    }


def sample_color(
    pdf_bytes: bytes,
    page_num: int,
    *,
    x: float,
    y: float,
    page_w: float,
    page_h: float,
    dpi: int = 300,
) -> dict[str, object]:
    """Sample sRGB at a PDF-space point.

    ``page_w`` / ``page_h`` are MediaBox dimensions in points;
    ``x`` / ``y`` are PDF-space (origin lower-left).
    """
    from codex_pdf.render.page import render_page

    png_bytes = render_page(pdf_bytes, page_num, dpi=dpi)
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img_w, img_h = img.size
    scale_x = img_w / page_w if page_w else 1.0
    scale_y = img_h / page_h if page_h else 1.0
    px_x = round(x * scale_x)
    px_y = round(img_h - y * scale_y)
    px_x = max(0, min(px_x, img_w - 1))
    px_y = max(0, min(px_y, img_h - 1))
    r, g, b = img.getpixel((px_x, px_y))
    return {
        "x": x,
        "y": y,
        "dpi": dpi,
        "rgb": [int(r), int(g), int(b)],
        "hex": f"#{r:02x}{g:02x}{b:02x}",
    }
