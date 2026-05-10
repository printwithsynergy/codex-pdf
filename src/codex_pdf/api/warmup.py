"""Worker warmup.

Run on FastAPI ``startup`` so the first real request doesn't pay the
cost of:

- lazy ``ThreadPoolExecutor`` thread creation,
- the first PyMuPDF / pikepdf C-module import + xref parse,
- module-level imports of color tables, structure helpers, etc.

The dummy work is run against a checked-in 516-byte single-page PDF
(`warmup.pdf`); we explicitly do not generate PDF bytes at runtime —
that would violate the "codex never produces PDFs" service boundary
asserted by ``scripts/produce_surface_audit.py``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_WARMUP_PDF = Path(__file__).resolve().parent / "warmup.pdf"


def warmup_worker() -> dict[str, float]:
    """Prime the extract pipeline. Best-effort; never raises.

    Returns a small dict with timing data so callers can record
    boot-time observability.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        from codex_pdf.extract.document import _EXTRACT_POOL
    except Exception:
        logger.warning("warmup: failed to import extract pool", exc_info=True)
        return {"total_s": 0.0}

    # Force the lazy pool to materialise its threads. Each noop returns
    # immediately; submitting ``max_workers`` of them guarantees every
    # thread is alive before the first real request lands.
    try:
        max_workers = _EXTRACT_POOL._max_workers  # type: ignore[attr-defined]
        futures = [_EXTRACT_POOL.submit(lambda: None) for _ in range(max_workers)]
        for f in futures:
            f.result(timeout=2.0)
        timings["pool_spawn_s"] = time.perf_counter() - started
    except Exception:
        logger.warning("warmup: pool spawn failed", exc_info=True)

    # Run a real Phase 1 + probe against the embedded fixture so every
    # module the request path touches is JIT-loaded.
    try:
        if _WARMUP_PDF.is_file():
            raw = _WARMUP_PDF.read_bytes()
            from codex_pdf.extract import (
                extract_document_fast,
                extract_probe_min,
                extract_probe_std,
            )

            t0 = time.perf_counter()
            extract_probe_min(raw)
            extract_probe_std(raw)
            timings["probe_s"] = time.perf_counter() - t0

            t0 = time.perf_counter()
            extract_document_fast(raw)
            timings["phase1_s"] = time.perf_counter() - t0
        else:
            logger.warning("warmup: %s missing; skipping extract priming", _WARMUP_PDF)
    except Exception:
        logger.warning("warmup: extract priming failed", exc_info=True)

    timings["total_s"] = time.perf_counter() - started
    logger.info("codex warmup complete: %s", {k: round(v, 4) for k, v in timings.items()})
    return timings
