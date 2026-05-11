"""Package version.

1.4.0 (minor): pulls spot-color authority and geometry primitives
into codex. New :mod:`codex_pdf.color` package owns the canonical
Pantone reference (formerly forked between lint-pdf and loupe-pdf),
the colour-math primitives (Lab→sRGB, CMYK→sRGB, ΔE76, ΔE2000), the
curated semantic spot map, and the host→codex→pantone→curated→hash
resolver ladder. New :mod:`codex_pdf.geom` package owns Box, Matrix,
Path, and tile_grid primitives in PDF user-space points (Clipper2-
backed via the optional ``[geom]`` extra).

New endpoints (additive, ``cache_backend`` healthcheck unchanged):

- ``POST /v1/color/resolve`` — host→codex→pantone→curated→hash ladder.
- ``POST /v1/color/match-pantone`` — nearest Pantone by ΔE2000.
- ``GET  /v1/color/inkbook`` — bundled curated + Pantone catalogue.
- ``POST /v1/geom/tile`` — imposition tile-grid preview.
- ``POST /v1/geom/intersect`` / ``union`` / ``difference`` — JSON
  polygon boolean ops (Clipper2 when available, rectangle math
  otherwise).

Producer-surface audit added (``scripts/produce_surface_audit.py``)
to lock the read-only invariant: codex MUST NOT write PDF bytes.
The audit fails CI on any ``pdf.save``, ``pikepdf.new``, ``pdfwrite``
Ghostscript invocation, ``%PDF-`` literal concatenation, or import
of pypdf / pdfrw / reportlab / fpdf / pdfkit / borb. The single
allowlisted save site is ``codex_pdf.render._common.apply_ocg_overrides``
(transient in-memory PDF fed straight to Ghostscript).

Schema is still v1.0.0 for the top-level codex-document contract.
The colour and geometry sections version independently:

- ``codex_pdf.color.COLOR_SCHEMA_VERSION`` = "1.0.0"
- ``codex_pdf.geom.GEOM_SCHEMA_VERSION``  = "1.0.0"

1.3.1 (prior): hardened the optional Redis cache so a misconfigured
or unreachable Redis service can never crash the codex API.
1.3.0 (prior): SSRF hardening + /v1/walk/type4 endpoint.
"""

VERSION = "1.8.0"
__version__ = VERSION
