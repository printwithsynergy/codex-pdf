"""Bundled Pantone reference database — load + lookup + ΔE-nearest match.

Source data lives at ``codex_pdf/color/data/pantone_reference.json``.
The JSON is an enriched 23k-entry catalogue spanning sixteen Pantone
libraries (Formula Guide Coated/Uncoated, Color Bridge Coated/
Uncoated, Extended Gamut, Metallics, Pastels & Neons, FHI Cotton TCX
/ Polyester / Nylon / Paper / Metallic Shimmers, SkinTone, CMYK
Coated/Uncoated). It is community-measured public-domain colour
science, NOT proprietary Pantone data.

Two access patterns:

- :func:`lookup_pantone_spot` — resolve by canonical name, with
  alternate-key fallback for ``PANTONE 485 C`` ↔ ``PANTONE 485C``.
- :func:`match_nearest_pantone` — search by Lab measurement, returning
  the nearest entry by CIEDE2000 ΔE. Library filters supported.

The reference is loaded once and cached per process. Hosts that ship
their own Pantone bundle (e.g. licensed Color Bridge data) can pass an
``extra_overrides`` map whose entries take precedence over the bundled
catalogue.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import Iterable, Iterator

from codex_pdf.color.color_math import LabTriplet, delta_e_2000
from codex_pdf.color.normalize import alternate_pantone_key, normalize_pantone_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PantoneEntry:
    """A single Pantone reference row, library-aware."""

    name: str
    library: str | None
    lab: tuple[float, float, float] | None
    cmyk_bridge: tuple[float, float, float, float] | None
    lab_source: str | None = None
    cmyk_source: str | None = None


@dataclass(frozen=True)
class PantoneReference:
    """Loaded Pantone catalogue + lookup index.

    ``by_normalized_name`` is built once at load-time to keep
    per-call lookups O(1) regardless of catalogue size.
    """

    meta: dict[str, object]
    entries: tuple[PantoneEntry, ...]
    by_normalized_name: dict[str, PantoneEntry]


def _coerce_entry(name: str, raw: dict[str, object]) -> PantoneEntry | None:
    lab_raw = raw.get("lab")
    if not isinstance(lab_raw, list) or len(lab_raw) != 3:
        lab: tuple[float, float, float] | None = None
    else:
        try:
            lab = (float(lab_raw[0]), float(lab_raw[1]), float(lab_raw[2]))
        except (TypeError, ValueError):
            lab = None
    cmyk_raw = raw.get("cmyk_bridge")
    if not isinstance(cmyk_raw, list) or len(cmyk_raw) != 4:
        cmyk: tuple[float, float, float, float] | None = None
    else:
        try:
            cmyk = (
                float(cmyk_raw[0]),
                float(cmyk_raw[1]),
                float(cmyk_raw[2]),
                float(cmyk_raw[3]),
            )
        except (TypeError, ValueError):
            cmyk = None
    if lab is None and cmyk is None:
        return None
    library = raw.get("library")
    library_str = str(library) if isinstance(library, str) else None
    lab_source = raw.get("lab_source")
    lab_source_str = str(lab_source) if isinstance(lab_source, str) else None
    cmyk_source = raw.get("cmyk_source")
    cmyk_source_str = str(cmyk_source) if isinstance(cmyk_source, str) else None
    return PantoneEntry(
        name=name,
        library=library_str,
        lab=lab,
        cmyk_bridge=cmyk,
        lab_source=lab_source_str,
        cmyk_source=cmyk_source_str,
    )


_cached_reference: PantoneReference | None = None


def load_pantone_reference() -> PantoneReference:
    """Load the bundled Pantone reference (cached after first call)."""
    global _cached_reference
    if _cached_reference is not None:
        return _cached_reference

    try:
        data_text = resources.files("codex_pdf.color.data").joinpath(
            "pantone_reference.json"
        ).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Pantone reference JSON missing from package data; using empty catalogue.")
        _cached_reference = PantoneReference(meta={}, entries=(), by_normalized_name={})
        return _cached_reference

    payload = json.loads(data_text)
    meta = payload.get("_meta") or {}
    raw_colors = payload.get("colors") or {}
    if not isinstance(raw_colors, dict):
        raw_colors = {}

    entries: list[PantoneEntry] = []
    index: dict[str, PantoneEntry] = {}
    for name, raw in raw_colors.items():
        if not isinstance(raw, dict):
            continue
        entry = _coerce_entry(name, raw)
        if entry is None:
            continue
        entries.append(entry)
        index[normalize_pantone_name(name)] = entry

    _cached_reference = PantoneReference(
        meta=meta if isinstance(meta, dict) else {},
        entries=tuple(entries),
        by_normalized_name=index,
    )
    return _cached_reference


def iter_pantone_entries(
    reference: PantoneReference,
    *,
    libraries: list[str] | None = None,
) -> Iterator[PantoneEntry]:
    """Yield entries matching the requested library filter.

    ``libraries=None`` defaults to the Formula Guide subset (Coated +
    Uncoated). Pass ``["*"]`` for everything, or specific names like
    ``["Pantone Color Bridge Coated"]``.
    """
    if libraries is None:
        wanted: set[str] | None = {
            "Pantone Formula Guide Coated",
            "Pantone Formula Guide Uncoated",
        }
    elif "*" in libraries:
        wanted = None
    else:
        wanted = set(libraries)
    for entry in reference.entries:
        if wanted is None or (entry.library is not None and entry.library in wanted):
            yield entry


def inkbook_manifest(
    reference: PantoneReference,
    *,
    libraries: list[str] | None = None,
) -> dict[str, object]:
    """Return manifest metadata for the inkbook payload."""
    raw_libraries = reference.meta.get("libraries")
    available = list(raw_libraries) if isinstance(raw_libraries, list) else []
    if libraries is None:
        included = ["Pantone Formula Guide Coated", "Pantone Formula Guide Uncoated"]
    elif "*" in libraries:
        included = available
    else:
        included = list(libraries)
    raw_count = sum(1 for _ in iter_pantone_entries(reference, libraries=libraries))
    return {
        "source": str(reference.meta.get("source") or ""),
        "license": str(reference.meta.get("license") or ""),
        "last_updated": str(reference.meta.get("last_updated") or ""),
        "available_libraries": available,
        "included_libraries": included,
        "included_count": raw_count,
        "total_count": int(reference.meta.get("count") or len(reference.entries)),
    }


def lookup_pantone_spot(
    spot_name: str,
    *,
    reference: PantoneReference | None = None,
    extra_overrides: dict[str, dict[str, object]] | None = None,
) -> PantoneEntry | None:
    """Resolve a spot name against the Pantone catalogue.

    Search order:

    1. ``extra_overrides`` (if provided) — exact normalised match.
    2. ``extra_overrides`` — alternate-key (toggle space before
       ``C/U/M/V`` finish suffix).
    3. Bundled catalogue — exact normalised match.
    4. Bundled catalogue — alternate-key.

    Returns ``None`` when the name isn't recognised.
    """
    ref = reference or load_pantone_reference()
    key = normalize_pantone_name(spot_name)
    alt = alternate_pantone_key(key)

    if extra_overrides:
        norm_extras: dict[str, PantoneEntry] = {}
        for name, raw in extra_overrides.items():
            if not isinstance(raw, dict):
                continue
            entry = _coerce_entry(name, raw)
            if entry is None:
                continue
            norm_extras[normalize_pantone_name(name)] = entry
        direct = norm_extras.get(key)
        if direct:
            return direct
        if alt is not None:
            entry = norm_extras.get(alt)
            if entry:
                return entry

    direct = ref.by_normalized_name.get(key)
    if direct:
        return direct
    if alt is not None:
        return ref.by_normalized_name.get(alt)
    return None


def match_nearest_pantone(
    lab: LabTriplet,
    *,
    reference: PantoneReference | None = None,
    libraries: list[str] | None = None,
) -> tuple[PantoneEntry, float] | None:
    """Find the nearest Pantone entry to a Lab measurement (CIEDE2000 ΔE).

    Iterates the requested library filter (defaults to Formula Guide
    Coated + Uncoated). Returns ``(entry, delta_e)`` or ``None`` if
    no entries with Lab values matched the filter.
    """
    ref = reference or load_pantone_reference()
    candidates: Iterable[PantoneEntry] = iter_pantone_entries(ref, libraries=libraries)
    best: tuple[PantoneEntry, float] | None = None
    for entry in candidates:
        if entry.lab is None:
            continue
        de = delta_e_2000(lab, entry.lab)
        if best is None or de < best[1]:
            best = (entry, de)
    return best
