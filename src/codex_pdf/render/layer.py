"""Codex OCG-isolated layer renderer.

Ported from
``lint-pdf/src/lintpdf/rendering.py:render_isolated_layer_tile``.
Returns an RGBA PNG where every pixel that doesn't carry the chosen
layer's ink is transparent — used by viewers that composite multiple
isolated layer tiles client-side via canvas ``source-over`` blending.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile

from PIL import Image

from codex_pdf.render._common import OCGError, apply_ocg_overrides, has_ghostscript

logger = logging.getLogger(__name__)


def _composite_via_separations_rgba(
    pdf_bytes: bytes, page_num: int, dpi: int
) -> bytes | None:
    """RGBA composite from per-channel separations.

    Pixels with no ink across any plate come back transparent so the
    browser's ``source-over`` compositor can stack multiple isolated
    layer tiles cleanly. Returns ``None`` when there are no
    separations to composite (caller should fall back to GS pngalpha).
    """
    import numpy as np

    from codex_pdf.render.separations import (
        _find_channel_tif,
        _pct_array_from_tiff,
        _run_tiffsep,
        list_separations,
    )

    try:
        spot_names = [
            s["name"] for s in list_separations(pdf_bytes) if s.get("type") == "spot"
        ]
    except Exception:
        spot_names = []

    plates: list[tuple[str, np.ndarray]] = []
    process_order = ["Cyan", "Magenta", "Yellow", "Black"]

    with tempfile.TemporaryDirectory(prefix="codex_layer_comp_") as tmpdir:
        try:
            output_base = _run_tiffsep(pdf_bytes, page_num, dpi, tmpdir)
        except Exception:
            logger.exception("layer composite: tiffsep failed")
            return None

        for ch in process_order:
            tif = _find_channel_tif(tmpdir, ch, output_base)
            if tif is not None:
                plates.append((ch, _pct_array_from_tiff(tif)))

        process_lower = {n.lower() for n in process_order}
        already = {name.lower() for name, _ in plates}
        for name in sorted(os.listdir(tmpdir)):
            if not name.endswith(".tif"):
                continue
            if "(" not in name or ")" not in name:
                continue
            spot = name[name.index("(") + 1 : name.rindex(")")]
            if not spot or spot.lower() in process_lower or spot.lower() in already:
                continue
            plates.append((spot, _pct_array_from_tiff(os.path.join(tmpdir, name))))
            already.add(spot.lower())

    if not plates:
        return None

    # Subtractive ink → RGB absorption. Same model lint/loupe use.
    ink_absorption_rgb: dict[str, tuple[int, int, int]] = {
        "Cyan": (255, 0, 0),
        "Magenta": (0, 255, 0),
        "Yellow": (0, 0, 255),
        "Black": (255, 255, 255),
    }

    def _spot_absorption_rgb(name: str) -> tuple[int, int, int]:
        lowered = name.strip().lower()
        exact: dict[str, tuple[int, int, int]] = {
            "black": (255, 255, 255),
            "k": (255, 255, 255),
            "cyan": (255, 0, 0),
            "c": (255, 0, 0),
            "magenta": (0, 255, 0),
            "m": (0, 255, 0),
            "yellow": (0, 0, 255),
            "y": (0, 0, 255),
            "white": (0, 0, 0),
        }
        if lowered in exact:
            return exact[lowered]

        patterns: list[tuple[str, tuple[int, int, int]]] = [
            ("cut", (0, 200, 200)),
            ("dieline", (0, 200, 200)),
            ("crease", (0, 200, 200)),
            ("perf", (0, 200, 200)),
            ("fold", (0, 200, 200)),
            ("foil", (128, 128, 128)),
            ("silver", (128, 128, 128)),
            ("gold", (40, 80, 200)),
            ("copper", (40, 80, 150)),
            ("varnish", (200, 200, 200)),
            ("matte", (200, 200, 200)),
            ("beige", (30, 80, 140)),
            ("tan", (30, 80, 140)),
            ("buff", (30, 60, 120)),
            ("cream", (10, 30, 80)),
            ("ivory", (10, 30, 80)),
            ("red", (0, 200, 200)),
            ("orange", (0, 150, 230)),
            ("blue", (220, 140, 0)),
            ("navy", (220, 140, 0)),
            ("green", (220, 40, 210)),
            ("teal", (220, 80, 120)),
            ("mint", (220, 80, 120)),
            ("purple", (120, 220, 80)),
            ("violet", (120, 220, 80)),
            ("pink", (40, 200, 100)),
            ("rose", (40, 200, 100)),
            ("brown", (80, 140, 200)),
            ("grey", (128, 128, 128)),
            ("gray", (128, 128, 128)),
            ("slate", (128, 128, 128)),
        ]
        for key, coef in patterns:
            if key in lowered:
                return coef

        h = 0
        for ch in name:
            h = ord(ch) + ((h << 5) - h)
        hue = abs(h) % 360
        s, light = 0.6, 0.45
        c = (1 - abs(2 * light - 1)) * s
        x = c * (1 - abs(((hue / 60) % 2) - 1))
        m = light - c / 2
        if hue < 60:
            r, g, b = c, x, 0
        elif hue < 120:
            r, g, b = x, c, 0
        elif hue < 180:
            r, g, b = 0, c, x
        elif hue < 240:
            r, g, b = 0, x, c
        elif hue < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        ink_r = round((r + m) * 255)
        ink_g = round((g + m) * 255)
        ink_b = round((b + m) * 255)
        return (255 - ink_r, 255 - ink_g, 255 - ink_b)

    height, width = plates[0][1].shape
    rgb = np.full((height, width, 3), 255.0, dtype=np.float32)

    for name, plate in plates:
        absorption = (
            ink_absorption_rgb.get(name)
            if name in process_order
            else _spot_absorption_rgb(name)
        )
        if absorption is None:
            continue
        tint = np.clip(plate, 0.0, 100.0) / 100.0
        for channel_idx, coef in enumerate(absorption):
            rgb[:, :, channel_idx] *= 1.0 - (tint * (coef / 255.0))

    rgb_uint8 = np.clip(rgb, 0.0, 255.0).astype(np.uint8)

    max_ink = np.zeros((rgb.shape[0], rgb.shape[1]), dtype=np.float32)
    for _name, plate in plates:
        np.maximum(max_ink, np.clip(plate, 0.0, 100.0), out=max_ink)
    alpha = (max_ink * 2.55).astype(np.uint8)
    rgba_uint8 = np.dstack([rgb_uint8, alpha])
    buf = io.BytesIO()
    Image.fromarray(rgba_uint8, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def render_layer(
    pdf_bytes: bytes,
    page_num: int,
    *,
    layer_index: int,
    all_layer_indices: list[int],
    dpi: int = 150,
) -> bytes:
    """Render a single OCG (layer) in isolation as an RGBA PNG.

    Args:
        pdf_bytes: Raw PDF bytes.
        page_num: 1-indexed page.
        layer_index: Index into ``/Root/OCProperties/OCGs``.
        all_layer_indices: Every OCG index on the page; everything
            else is forced hidden.
        dpi: Render resolution.

    Returns:
        RGBA PNG bytes (transparent where the layer doesn't paint).
    """
    if not has_ghostscript():
        raise RuntimeError(
            "render_layer requires Ghostscript. Install ghostscript >= 9.50."
        )
    if layer_index not in all_layer_indices:
        raise OCGError(
            f"layer_index={layer_index} not in all_layer_indices={all_layer_indices}"
        )

    ocg_off = [i for i in all_layer_indices if i != layer_index]
    pdf_isolated = apply_ocg_overrides(pdf_bytes, [layer_index], ocg_off)

    try:
        from codex_pdf.render.separations import list_separations

        if any(s.get("type") == "spot" for s in list_separations(pdf_isolated)):
            tile = _composite_via_separations_rgba(pdf_isolated, page_num, dpi)
            if tile is not None:
                return tile
    except Exception:
        logger.exception(
            "render_layer: software composite raised; falling back to Ghostscript pngalpha"
        )

    with tempfile.TemporaryDirectory(prefix="codex_layer_") as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        png_path = os.path.join(tmpdir, "page.png")
        with open(pdf_path, "wb") as fh:
            fh.write(pdf_isolated)

        cmd = [
            "gs",
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=pngalpha",
            "-sColorConversionStrategy=RGB",
            "-dRenderIntent=0",
            "-dSimulateOverprint=true",
            "-dOverprint=/simulate",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            f"-r{dpi}",
            f"-dFirstPage={page_num}",
            f"-dLastPage={page_num}",
            f"-sOutputFile={png_path}",
            pdf_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Ghostscript layer-tile render timed out for page {page_num} layer {layer_index}",
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[:500]
            raise RuntimeError(
                f"Ghostscript layer-tile render failed (rc={proc.returncode}): {stderr}"
            )
        if not os.path.exists(png_path):
            raise RuntimeError(
                f"Ghostscript produced no output for page {page_num} layer {layer_index}"
            )
        with open(png_path, "rb") as fh:
            return fh.read()


__all__ = ["OCGError", "render_layer"]
