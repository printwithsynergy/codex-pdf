"""Package version.

1.12.0 (minor): AI Signal Campaign — Phase 1.5 lands the codex-
vision-sidecar. A new :mod:`codex_pdf.vision` package ships a
FastAPI service (``python -m codex_pdf.vision``) plus the HTTP
client (``codex_pdf.vision.client``) the main API uses to call it.
First extractor is **perceptual hashing** (CPU-pure, no ONNX
required); subsequent 1.x releases add NudeNet + CLIP without
shape changes to this contract.

The vision sidecar is **optional**. When ``CODEX_VISION_URL`` is
unset on the main API service codex degrades gracefully (vision-
sourced signal kinds stay empty + a ``vision_unavailable`` warning
lands on the response).

New optional extra: ``codex-pdf[vision]`` adds ``imagehash`` +
``Pillow`` for the sidecar service. The main API only needs
``httpx`` (now in base deps) to call into it.

Deploy: ``railway.vision.toml`` ships the start command +
restart-policy + scale-to-zero defaults. Provision as a second
service in the same Railway project as
``codex-pdf-<host>-sidecar``; set ``CODEX_VISION_URL`` on the main
service to its private-network URL.

1.11.0 (minor): AI Signal Campaign — Phase 1 implementation lands.
The six extractors frozen by 1.10.0's contract are now wired:

- :mod:`codex_pdf.ai.language` — page dominant-language detection
  (Claude Haiku, text-only)
- :mod:`codex_pdf.ai.logos` — brand / logo detection (Claude Sonnet
  vision)
- :mod:`codex_pdf.ai.symbols` — regulatory / packaging / safety
  symbol detection (Claude Sonnet vision)
- :mod:`codex_pdf.ai.barcodes` — pyzbar + pylibdmtx pure-CPU lane
  (no Claude calls)
- :mod:`codex_pdf.ai.classification` — document-level classification
  (Claude Haiku, text-only)
- :mod:`codex_pdf.ai.spell` — unknown-word candidates (Claude Haiku,
  text-only)

The :class:`~codex_pdf.ai.budget.AiBudget` enforces a per-request
hard cap (env ``CODEX_AI_COST_CAP_USD_PER_REQUEST``, default
``$0.10``); the next call's projection is checked BEFORE the call
goes out so a single huge PDF can't blow the budget. ``cap`` hit →
``CodexWarning(code="ai_budget_exceeded", scope="signals.<kind>")``
and remaining signals stay empty.

The warning catalogue evolves slightly from 1.10.0:

- ``ai_signals_pending_impl`` was Phase 0 only and no longer emits.
- ``ai_missing_credentials`` is new — operator opted in but
  ``anthropic`` SDK isn't importable or ``ANTHROPIC_API_KEY`` is
  unset. Signal fields stay empty.
- ``ai_tier`` (advisory) now lands on every successful AI run; its
  message carries the tier label (``cpu+claude`` for Tier 1,
  ``gpu`` for Tier 2 when ``CODEX_AI_GPU_URL`` is set) plus the
  realised dollar spend.

The ``GET /v1/documents/{pdf_hash}/signals/{kind}`` endpoint
returns real data now — Phase 0's 501 stub is gone. Page-scoped
kinds accept ``?page_index=N`` (default 0). The endpoint hits the
per-kind cache first; on miss it re-runs only the requested
extractor.

Schema unchanged at 1.3.0 — the contract was finalised in 1.10.0.

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

VERSION = "1.12.0"
__version__ = VERSION
