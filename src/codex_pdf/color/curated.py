"""Curated semantic spot-ink map (Cut, Dieline, Varnish, Foil…).

Print artwork routinely declares spots whose names describe a *role*
(Cut, Dieline, Bleed, Varnish, White, Foil, Silver, Gold) rather than
a Pantone reference. None of those resolve through the Pantone
database, but every shop expects a consistent, recognisable swatch —
``Cut`` always magenta, ``Dieline`` always violet, ``Varnish`` always
a translucent gloss tint, etc.

The mapping uses substring matching against the lower-cased name
after stripping punctuation. Order matters: more specific names come
first (``silver`` before ``gray``).

Mirrors ``loupe-pdf/host/spotColor/curated.ts`` byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedSpotEntry:
    """Curated swatch entry — RGB + tokens that match this entry."""

    rgb: tuple[int, int, int]
    tokens: tuple[str, ...]


CURATED_SPOT_ENTRIES: tuple[CuratedSpotEntry, ...] = (
    CuratedSpotEntry(rgb=(236, 0, 140), tokens=("cutcontour", "cut contour", "cutter")),
    CuratedSpotEntry(rgb=(236, 0, 140), tokens=("cut ",)),
    CuratedSpotEntry(rgb=(236, 0, 140), tokens=("cut-line", "cutline")),
    CuratedSpotEntry(
        rgb=(148, 0, 211),
        tokens=("dieline", "die-line", "die line", "die cut", "diecut"),
    ),
    CuratedSpotEntry(rgb=(255, 165, 0), tokens=("bleed",)),
    CuratedSpotEntry(rgb=(0, 112, 192), tokens=("safe area", "safe-area", "safety")),
    CuratedSpotEntry(rgb=(60, 180, 75), tokens=("fold",)),
    CuratedSpotEntry(rgb=(128, 0, 128), tokens=("perf", "perforation")),
    CuratedSpotEntry(rgb=(220, 20, 60), tokens=("score",)),
    CuratedSpotEntry(rgb=(70, 130, 180), tokens=("registration",)),
    CuratedSpotEntry(rgb=(220, 220, 230), tokens=("varnish", "gloss", "matte", "satin")),
    CuratedSpotEntry(rgb=(240, 240, 245), tokens=("spot uv", "spot-uv", "uv coat")),
    CuratedSpotEntry(rgb=(248, 248, 252), tokens=("white",)),
    CuratedSpotEntry(rgb=(200, 200, 200), tokens=("aqueous", "primer", "overprint clear")),
    CuratedSpotEntry(rgb=(165, 165, 175), tokens=("foil",)),
    CuratedSpotEntry(rgb=(192, 192, 192), tokens=("silver", "metallic silver")),
    CuratedSpotEntry(rgb=(212, 175, 55), tokens=("gold", "metallic gold")),
    CuratedSpotEntry(rgb=(184, 115, 51), tokens=("copper",)),
    CuratedSpotEntry(rgb=(80, 50, 20), tokens=("bronze",)),
)


def lookup_curated_spot(spot_name: str) -> CuratedSpotEntry | None:
    """Resolve a curated swatch from a spot name, or ``None`` when none matches.

    Matching is substring against the lower-cased input. The first
    matching entry wins.
    """
    haystack = spot_name.lower()
    for entry in CURATED_SPOT_ENTRIES:
        for token in entry.tokens:
            if token in haystack:
                return entry
    return None
