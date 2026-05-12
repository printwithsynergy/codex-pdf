"""Perceptual hashing for codex page renders.

Uses ``imagehash``'s 64-bit pHash by default. The hash is content-
addressed: two pages that render identically yield identical
hashes; near-duplicates yield hashes with low Hamming distance.

This is CPU-pure — no ML model file, no GPU. Latency is dominated
by the DCT calculation; ~1ms / megapixel on a 2 vCPU container.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Final

logger = logging.getLogger(__name__)

PHASH_ALGORITHM: Final[str] = "phash-64"
"""Algorithm identifier returned alongside every hash. Bumps when
the underlying library changes its semantics (different hash size,
different DCT block size, etc.)."""


def compute_phash(png_bytes: bytes) -> str | None:
    """Return the 64-bit perceptual hash of ``png_bytes`` as hex.

    Returns ``None`` when imagehash isn't installed or the input
    isn't a parsable image — caller handles the empty result.
    """
    if not png_bytes:
        return None
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        logger.warning("imagehash / Pillow not installed; pHash extractor inactive")
        return None
    try:
        with Image.open(BytesIO(png_bytes)) as img:
            hash_obj = imagehash.phash(img)
            return str(hash_obj)
    except Exception:
        logger.exception("compute_phash failed")
        return None


def hamming_distance(hex_a: str, hex_b: str) -> int | None:
    """Hamming distance between two pHash hex strings.

    Returns ``None`` when either hash isn't parseable. Useful for
    consumers that want to dedup pages without re-running the
    hash — a distance ≤ 5 typically indicates near-duplicates.
    """
    try:
        a = int(hex_a, 16)
        b = int(hex_b, 16)
    except (TypeError, ValueError):
        return None
    return bin(a ^ b).count("1")
