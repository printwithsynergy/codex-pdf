"""Cheapest-possible PDF facts.

Two tiers — both run on PyMuPDF only and skip every pikepdf pass:

- :func:`extract_probe_min` — page count + first page dimensions +
  encryption flag. Target latency: <20 ms warm, <50 ms cold.
- :func:`extract_probe_std` — adds full page-dimension list +
  Info dict + PDF version. Target latency: <80-150 ms.

Used by ``POST /v1/probe`` (two-event SSE) so callers get a
sub-50 ms acknowledgement long before Phase 1 / Phase 2 extract is
ready. Outputs are deterministic and content-addressed by the same
``cache_key`` machinery as the full extract.
"""

from __future__ import annotations

from typing import Any


def _open(raw: bytes):
    import fitz

    return fitz.open(stream=raw, filetype="pdf")


def _page_dims(page: Any) -> dict[str, float]:
    rect = page.rect
    return {
        "width_pts": float(rect.width),
        "height_pts": float(rect.height),
        "rotation": int(getattr(page, "rotation", 0) or 0),
    }


def extract_probe_min(raw: bytes) -> dict[str, Any]:
    """Bare-minimum probe: page count, first-page dimensions, encryption.

    All fields are best-effort; on any failure the corresponding value
    is ``None`` / ``False`` / ``0`` rather than raising. The caller
    surfaces this as the first SSE event.
    """
    try:
        doc = _open(raw)
    except Exception:
        return {
            "page_count": 0,
            "first_page_dims": None,
            "encrypted": False,
        }
    try:
        page_count = doc.page_count
        encrypted = bool(getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False))
        first_dims: dict[str, float] | None = None
        if page_count > 0:
            try:
                first_dims = _page_dims(doc[0])
            except Exception:
                first_dims = None
        return {
            "page_count": int(page_count),
            "first_page_dims": first_dims,
            "encrypted": encrypted,
        }
    finally:
        try:
            doc.close()
        except Exception:
            pass


def extract_probe_std(raw: bytes) -> dict[str, Any]:
    """Standard probe: full page dim list + metadata + version.

    Target latency: 80-150 ms even on 100-page documents. Skips fonts,
    images, annotations, and every pikepdf pass.
    """
    out: dict[str, Any] = {
        "page_count": 0,
        "page_dims": [],
        "info": {},
        "pdf_version": "unknown",
        "encrypted": False,
    }
    try:
        doc = _open(raw)
    except Exception:
        return out
    try:
        out["page_count"] = int(doc.page_count)
        out["encrypted"] = bool(
            getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False)
        )
        version_raw = getattr(doc, "pdf_version", None)
        if isinstance(version_raw, str) and version_raw:
            out["pdf_version"] = version_raw

        try:
            metadata = dict(doc.metadata or {})
        except Exception:
            metadata = {}
        # Trim to the canonical Info dict subset used by Codex; drop
        # every other PyMuPDF metadata key so the probe stays small.
        out["info"] = {
            k: metadata.get(k)
            for k in ("title", "author", "subject", "keywords", "creator", "producer", "format")
            if metadata.get(k)
        }

        dims: list[dict[str, float]] = []
        for i in range(out["page_count"]):
            try:
                dims.append(_page_dims(doc[i]))
            except Exception:
                dims.append({"width_pts": 0.0, "height_pts": 0.0, "rotation": 0})
        out["page_dims"] = dims
        return out
    finally:
        try:
            doc.close()
        except Exception:
            pass
