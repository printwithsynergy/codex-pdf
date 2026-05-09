"""Codex color authority — single source of truth for spot-ink resolution.

This package replaces the per-consumer Pantone / colour-math forks
that previously lived in ``loupe-pdf/host/spotColor/`` and
``lint-pdf/src/lintpdf/profiles/icc/pantone_manager``. Both consumers
now call into this module (lint in-process, loupe via the codex HTTP
surface) so the Pantone reference data, the Lab/CMYK/RGB conversion
maths, and the resolver-precedence ladder live in exactly one place.

Public surface:

- :func:`resolve_spot_swatch_color` — host → codex → pantone → curated
  → hash precedence ladder, returning a ``SpotSwatchResolution`` with
  rgb/lab/cmyk/source/pantone_name fields populated when known.
- :func:`match_pantone` — find the nearest Pantone entry to a given
  Lab/CMYK/RGB measurement using CIEDE2000 ΔE.
- :func:`load_inkbook` — return the bundled curated catalog plus a
  manifest of Pantone libraries shipped with this codex build.
- :func:`normalize_pantone_name` / :func:`alternate_pantone_key` —
  canonicalisation helpers (mirrored verbatim by the TS client).
- :func:`lab_d50_to_srgb` / :func:`cmyk_to_srgb_naive` /
  :func:`delta_e_2000` — the colour-math primitives used by the
  resolver. Exposed for tests + power consumers.

Schema versioning: this package bumps :data:`COLOR_SCHEMA_VERSION`
independently of the top-level ``codex-document`` schema. The HTTP
endpoints carry the section version on every response so SDK
consumers can pin the surface they validate against.
"""

from __future__ import annotations

from codex_pdf.color.color_math import (
    cmyk_to_srgb_naive,
    delta_e_76,
    delta_e_2000,
    lab_d50_to_srgb,
    srgb_decode,
)
from codex_pdf.color.curated import (
    CURATED_SPOT_ENTRIES,
    CuratedSpotEntry,
    lookup_curated_spot,
)
from codex_pdf.color.normalize import alternate_pantone_key, normalize_pantone_name
from codex_pdf.color.pantone import (
    PantoneEntry,
    PantoneReference,
    inkbook_manifest,
    iter_pantone_entries,
    load_pantone_reference,
    lookup_pantone_spot,
    match_nearest_pantone,
)
from codex_pdf.color.resolver import (
    CodexSpotIntent,
    SpotInkOverride,
    SpotSwatchResolution,
    SpotSwatchSource,
    hash_hue_rgb,
    resolve_spot_swatch_color,
)

COLOR_SCHEMA_VERSION = "1.1.0"
"""Per-section schema version for the ``/v1/color/*`` HTTP surface.

Bumped independently of the top-level codex-document schema so a
purely-additive colour API change does not force every consumer to
re-validate their CodexDocument schema baselines.
"""


def load_inkbook(*, libraries: list[str] | None = None) -> dict[str, object]:
    """Return the bundled inkbook (curated + Pantone) as a JSON-ready dict.

    The result includes the curated semantic catalogue, Pantone
    libraries (defaulting to Formula Guide Coated + Uncoated), and
    provenance metadata. Callers that want the full 23k-entry catalog
    can pass ``libraries=["*"]``.
    """
    ref = load_pantone_reference()
    manifest = inkbook_manifest(ref, libraries=libraries)
    pantone_entries: list[dict[str, object]] = []
    for entry in iter_pantone_entries(ref, libraries=libraries):
        item: dict[str, object] = {
            "name": entry.name,
            "library": entry.library,
        }
        if entry.lab is not None:
            item["lab"] = list(entry.lab)
        if entry.cmyk_bridge is not None:
            item["cmyk_bridge"] = list(entry.cmyk_bridge)
        if entry.lab_source is not None:
            item["lab_source"] = entry.lab_source
        if entry.cmyk_source is not None:
            item["cmyk_source"] = entry.cmyk_source
        pantone_entries.append(item)
    curated = [
        {"rgb": list(entry.rgb), "tokens": list(entry.tokens)}
        for entry in CURATED_SPOT_ENTRIES
    ]
    return {
        "schema_version": COLOR_SCHEMA_VERSION,
        "manifest": manifest,
        "pantone": pantone_entries,
        "curated": curated,
    }


__all__ = [
    "COLOR_SCHEMA_VERSION",
    "CURATED_SPOT_ENTRIES",
    "CodexSpotIntent",
    "CuratedSpotEntry",
    "PantoneEntry",
    "PantoneReference",
    "SpotInkOverride",
    "SpotSwatchResolution",
    "SpotSwatchSource",
    "alternate_pantone_key",
    "cmyk_to_srgb_naive",
    "delta_e_2000",
    "delta_e_76",
    "hash_hue_rgb",
    "inkbook_manifest",
    "iter_pantone_entries",
    "lab_d50_to_srgb",
    "load_inkbook",
    "load_pantone_reference",
    "lookup_curated_spot",
    "lookup_pantone_spot",
    "match_nearest_pantone",
    "normalize_pantone_name",
    "resolve_spot_swatch_color",
    "srgb_decode",
]
