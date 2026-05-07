"""Codex render core.

Owns every PDF byte-level raster path used by Think Neverland tools:
page raster (with overprint simulation + OCG overrides), per-channel
separations, TAC heatmap, OCG-isolated layer tiles, point density
sampling, point color sampling, and content-stream walks.

The Python render functions are also exposed via FastAPI in
:mod:`codex_pdf.api`. The :class:`codex_pdf.client.HttpClient` chooses
between HTTP and a local in-process call into these modules — that is
the supported "local fallback" pathway when ``CODEX_API_BASE`` is unset.
"""

from codex_pdf.render.layer import OCGError, render_layer
from codex_pdf.render.page import render_page
from codex_pdf.render.separations import (
    list_separations,
    render_heatmap,
    render_separations,
    sample_color,
    sample_density,
)

__all__ = [
    "OCGError",
    "list_separations",
    "render_heatmap",
    "render_layer",
    "render_page",
    "render_separations",
    "sample_color",
    "sample_density",
]
