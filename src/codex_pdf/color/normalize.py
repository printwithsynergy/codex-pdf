"""Pantone name canonicalisation.

Mirrors ``loupe-pdf/host/spotColor/normalize.ts`` and the legacy
``lintpdf.profiles.icc.pantone_manager._normalize_pantone_name`` so
the same key resolves regardless of which surface the call originates
from. The TS client re-exports identical helpers built on these rules.

Examples:
    >>> normalize_pantone_name("Pantone 485 C")
    'PANTONE 485 C'
    >>> normalize_pantone_name("  pantone  485c ")
    'PANTONE 485C'
    >>> alternate_pantone_key("PANTONE 485 C")
    'PANTONE 485C'
    >>> alternate_pantone_key("PANTONE 485C")
    'PANTONE 485 C'
    >>> alternate_pantone_key("PANTONE Reflex Blue")  # no finish suffix
    'PANTONE REFLEX BLUE'
    >>> alternate_pantone_key("ECM Magenta")  # not a Pantone name
"""

from __future__ import annotations

import re

_SPACE_COLLAPSE = re.compile(r"\s+")
_SUFFIXED_WITH_SPACE = re.compile(r"^(PANTONE\s+.+?)\s+([CUMV])$")
_SUFFIXED_NO_SPACE = re.compile(r"^(PANTONE\s+.+\S)([CUMV])$")
_PMS_PREFIX = re.compile(r"^\s*(?:PMS|P\.M\.S\.|P\s*M\s*S)\s+", re.IGNORECASE)


def normalize_pantone_name(name: str) -> str:
    """Canonicalise a Pantone-style name to UPPERCASE + collapsed spaces.

    Idempotent: safe to call on already-normalised values. Does not
    rewrite trailing-suffix spacing — that is a separate alternate-key
    operation handled by :func:`alternate_pantone_key` so the resolver
    can try both forms in order.

    Also rewrites the legacy ``"PMS 485"`` short form into the
    canonical ``"PANTONE 485"`` prefix so the same lookup resolves.
    """
    s = name.strip()
    s = _PMS_PREFIX.sub("PANTONE ", s)
    s = s.upper()
    s = _SPACE_COLLAPSE.sub(" ", s)
    return s


def alternate_pantone_key(key: str) -> str | None:
    """Try the alternate spacing around a trailing finish suffix.

    Returns ``None`` when the input doesn't carry a recognised finish
    suffix (one of ``C``, ``U``, ``M``, ``V``). The bundled Pantone
    JSON ships with the ``"PANTONE 485 C"`` form, but some PDF
    producers emit ``"PANTONE 485C"``; both should resolve to the
    same entry.

    The match priority intentionally mirrors the TS implementation:
    the with-space form is checked first because the no-space regex
    would also match a body ending in space + suffix and produce
    ``"PANTONE 485  C"`` (two spaces).
    """
    with_space = _SUFFIXED_WITH_SPACE.match(key)
    if with_space:
        return f"{with_space.group(1)}{with_space.group(2)}"
    no_space = _SUFFIXED_NO_SPACE.match(key)
    if no_space:
        return f"{no_space.group(1)} {no_space.group(2)}"
    return None
