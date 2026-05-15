"""Spot-ink swatch resolver — host → codex → pantone → curated → ai → hash.

The resolver is the single in-process entry point that lint and loupe
both call (lint imports it directly; loupe calls the
``POST /v1/color/resolve`` endpoint backed by this same function).
The precedence ladder is:

1. **host** — explicit override the embedding host passed in.
2. **codex** — Lab/CMYK/RGB the codex extractor surfaced for the
   spot colorant on its parent colour space (or a canonical
   ``pantone_name`` it recognised).
3. **pantone** — bundled Pantone reference, looked up by canonical
   name (``PANTONE 485 C`` etc.) — first hit wins.
4. **curated** — semantic spot map (cut, dieline, varnish, foil…)
   so role-named spots get a recognisable swatch.
5. **ai** — Claude Haiku estimates CIE Lab from the ink name.
   Runs only when ``CODEX_AI_ENABLED=1`` and ``ANTHROPIC_API_KEY``
   are set. Cached per ink name (``lru_cache``) so the LLM bill is
   paid once per unique name per process. Tagged ``source: "ai"`` so
   UIs can show an "AI-estimated" badge when the codex/Pantone path
   didn't have data for this ink.
6. **hash** — final tie-breaker. Hash-derived hue, returned only
   with ``source: "hash"`` so UIs can mark the swatch as approximate.

The result always includes a concrete ``rgb``; downstream code never
needs a fallback. ``lab``, ``cmyk``, and ``pantone_name`` are
populated whenever the chosen source carried that information.

Mirrors ``loupe-pdf/host/spotColor/resolveSpotSwatchColor.ts``
verbatim (same precedence, same hash hue algorithm) so output is
identical between the in-process Python path and the in-browser TS
path.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from codex_pdf.color.color_math import (
    CmykQuad,
    LabTriplet,
    RgbTriplet,
    cmyk_to_srgb_naive,
    lab_d50_to_srgb,
)
from codex_pdf.color.curated import lookup_curated_spot
from codex_pdf.color.pantone import (
    PantoneEntry,
    PantoneReference,
    lookup_pantone_spot,
)

SpotSwatchSource = Literal["host", "codex", "pantone", "curated", "ai", "hash"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpotInkOverride:
    """Per-ink override the embedding host can supply.

    Any of ``rgb`` / ``lab`` / ``cmyk`` may be set; precedence within
    the override is rgb → lab → cmyk. ``pantone_name`` is optional
    and surfaces in the resolved swatch's ``pantone_name`` so brand
    re-mappings keep their canonical label.
    """

    rgb: RgbTriplet | None = None
    lab: LabTriplet | None = None
    cmyk: CmykQuad | None = None
    pantone_name: str | None = None


@dataclass(frozen=True)
class CodexSpotIntent:
    """Per-ink intent the codex extractor surfaced.

    Mirrors the additive ``CodexSpotColorant.{lab,cmyk,rgb,pantone_name}``
    contract on the v1 schema. All fields optional; the resolver
    picks the strongest signal available.
    """

    rgb: RgbTriplet | None = None
    lab: LabTriplet | None = None
    cmyk: CmykQuad | None = None
    pantone_name: str | None = None


@dataclass(frozen=True)
class SpotSwatchResolution:
    """Resolved swatch + provenance.

    ``rgb`` is always populated. The remaining fields appear only
    when the chosen source carried them, so UIs can show "Lab values
    available" / "from Color Bridge CMYK" badges accurately.
    """

    rgb: RgbTriplet
    source: SpotSwatchSource
    lab: LabTriplet | None = None
    cmyk: CmykQuad | None = None
    pantone_name: str | None = None


@dataclass(frozen=True)
class _ResolveOptions:
    host_override: SpotInkOverride | None = None
    codex_intent: CodexSpotIntent | None = None
    extra_pantone_overrides: dict[str, dict[str, object]] | None = None
    reference: PantoneReference | None = None
    extra_curated_tokens: tuple[tuple[RgbTriplet, tuple[str, ...]], ...] = field(default_factory=tuple)


def _try_host(override: SpotInkOverride | None) -> SpotSwatchResolution | None:
    if override is None:
        return None
    if override.rgb is not None:
        return SpotSwatchResolution(
            rgb=override.rgb,
            source="host",
            lab=override.lab,
            cmyk=override.cmyk,
            pantone_name=override.pantone_name,
        )
    if override.lab is not None:
        return SpotSwatchResolution(
            rgb=lab_d50_to_srgb(override.lab),
            source="host",
            lab=override.lab,
            cmyk=override.cmyk,
            pantone_name=override.pantone_name,
        )
    if override.cmyk is not None:
        return SpotSwatchResolution(
            rgb=cmyk_to_srgb_naive(override.cmyk),
            source="host",
            cmyk=override.cmyk,
            pantone_name=override.pantone_name,
        )
    return None


def _from_pantone_entry(entry: PantoneEntry) -> SpotSwatchResolution:
    if entry.lab is not None:
        return SpotSwatchResolution(
            rgb=lab_d50_to_srgb(entry.lab),
            source="pantone",
            lab=entry.lab,
            cmyk=entry.cmyk_bridge,
            pantone_name=entry.name,
        )
    if entry.cmyk_bridge is not None:
        return SpotSwatchResolution(
            rgb=cmyk_to_srgb_naive(entry.cmyk_bridge),
            source="pantone",
            cmyk=entry.cmyk_bridge,
            pantone_name=entry.name,
        )
    # Defensive — every loaded entry has lab or cmyk.
    return SpotSwatchResolution(
        rgb=hash_hue_rgb(entry.name),
        source="pantone",
        pantone_name=entry.name,
    )


def _try_codex(
    intent: CodexSpotIntent | None,
    *,
    reference: PantoneReference | None,
    extra_pantone_overrides: dict[str, dict[str, object]] | None,
) -> SpotSwatchResolution | None:
    if intent is None:
        return None
    if intent.rgb is not None:
        return SpotSwatchResolution(
            rgb=intent.rgb,
            source="codex",
            lab=intent.lab,
            cmyk=intent.cmyk,
            pantone_name=intent.pantone_name,
        )
    if intent.lab is not None:
        return SpotSwatchResolution(
            rgb=lab_d50_to_srgb(intent.lab),
            source="codex",
            lab=intent.lab,
            cmyk=intent.cmyk,
            pantone_name=intent.pantone_name,
        )
    if intent.cmyk is not None:
        return SpotSwatchResolution(
            rgb=cmyk_to_srgb_naive(intent.cmyk),
            source="codex",
            cmyk=intent.cmyk,
            pantone_name=intent.pantone_name,
        )
    if intent.pantone_name:
        entry = lookup_pantone_spot(
            intent.pantone_name,
            reference=reference,
            extra_overrides=extra_pantone_overrides,
        )
        if entry is not None:
            return _from_pantone_entry(entry)
    return None


def _try_pantone(
    spot_name: str,
    *,
    reference: PantoneReference | None,
    extra_pantone_overrides: dict[str, dict[str, object]] | None,
) -> SpotSwatchResolution | None:
    entry = lookup_pantone_spot(
        spot_name,
        reference=reference,
        extra_overrides=extra_pantone_overrides,
    )
    if entry is None:
        return None
    return _from_pantone_entry(entry)


def _try_curated(
    spot_name: str,
    extras: tuple[tuple[RgbTriplet, tuple[str, ...]], ...],
) -> SpotSwatchResolution | None:
    haystack = spot_name.lower()
    for rgb, tokens in extras:
        for token in tokens:
            if token in haystack:
                return SpotSwatchResolution(rgb=rgb, source="curated")
    entry = lookup_curated_spot(spot_name)
    if entry is None:
        return None
    return SpotSwatchResolution(rgb=entry.rgb, source="curated")


def hash_hue_rgb(name: str) -> RgbTriplet:
    """Stable hash-of-name → HSL → sRGB (matches the TS implementation).

    Identical algorithm to the legacy fallback so existing visual
    identities don't shuffle when a truly unknown spot is rendered.
    Always tagged ``source: "hash"`` in resolver output.
    """
    h = 0
    for ch in name:
        h = (ord(ch) + ((h << 5) - h)) & 0xFFFFFFFF
    if h & 0x80000000:
        h -= 0x100000000
    hue = abs(h) % 360
    s = 0.7
    l = 0.45
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs(((hue / 60.0) % 2) - 1))
    m = l - c / 2.0
    if hue < 60:
        r, g, b = c, x, 0.0
    elif hue < 120:
        r, g, b = x, c, 0.0
    elif hue < 180:
        r, g, b = 0.0, c, x
    elif hue < 240:
        r, g, b = 0.0, x, c
    elif hue < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (
        max(0, min(255, round((r + m) * 255))),
        max(0, min(255, round((g + m) * 255))),
        max(0, min(255, round((b + m) * 255))),
    )


_AI_SYSTEM = (
    "You are a color science expert. Given a spot printing ink name, return your "
    "best estimate of its CIE Lab (D50 illuminant) color values. "
    "Output ONLY valid JSON — no prose, no markdown: "
    '{"L": <0..100>, "a": <-128..127>, "b": <-128..127>}. '
    "If the name carries no color information (e.g. 'Die', 'Cut', 'Crease', "
    "'Emboss', 'Varnish'), return null."
)


@lru_cache(maxsize=512)
def _ai_lab_estimate(spot_name: str) -> tuple[float, float, float] | None:
    """Call Claude Haiku to estimate CIE Lab for an unknown spot ink name.

    Cached per ink name (process-lifetime) so each unique name is queried
    at most once per process. Requires ``CODEX_AI_ENABLED=1`` and
    ``ANTHROPIC_API_KEY`` in the environment; returns ``None`` silently
    when either is absent or the call fails.
    """
    if os.environ.get("CODEX_AI_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=10.0)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=64,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": f"Spot ink name: {spot_name!r}"}],
        )
        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        ).strip()
    except Exception as exc:
        _logger.debug("ai_lab_estimate failed for %r: %s", spot_name, exc)
        return None
    if not text or text.lower() == "null":
        return None
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group())
        if data is None:
            return None
        return (float(data["L"]), float(data["a"]), float(data["b"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _try_ai(spot_name: str) -> SpotSwatchResolution | None:
    lab = _ai_lab_estimate(spot_name)
    if lab is None:
        return None
    return SpotSwatchResolution(
        rgb=lab_d50_to_srgb(lab),
        source="ai",
        lab=lab,
    )


def resolve_spot_swatch_color(
    spot_name: str,
    *,
    host_override: SpotInkOverride | None = None,
    codex_intent: CodexSpotIntent | None = None,
    extra_pantone_overrides: dict[str, dict[str, object]] | None = None,
    reference: PantoneReference | None = None,
    extra_curated_tokens: tuple[tuple[RgbTriplet, tuple[str, ...]], ...] = (),
) -> SpotSwatchResolution:
    """Resolve a spot-ink name to a display swatch + provenance.

    Process inks (Cyan, Magenta, Yellow, Black) should NOT go through
    this function — they keep their canonical CMYK primaries. The
    resolver is reserved for ``Separation`` / ``DeviceN`` colorants
    whose intent isn't fixed by the colour model.
    """
    return (
        _try_host(host_override)
        or _try_codex(
            codex_intent,
            reference=reference,
            extra_pantone_overrides=extra_pantone_overrides,
        )
        or _try_pantone(
            spot_name,
            reference=reference,
            extra_pantone_overrides=extra_pantone_overrides,
        )
        or _try_curated(spot_name, extra_curated_tokens)
        or _try_ai(spot_name)
        or SpotSwatchResolution(rgb=hash_hue_rgb(spot_name), source="hash")
    )
