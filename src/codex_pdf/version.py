"""Package version.

1.17.0 (minor): Effective DPI now uses actual placed image rect.

The previous _estimate_dpi() used full page dimensions as a proxy,
giving meaningless results (e.g. a 72px image on an 8.5in page
showed ~8 DPI instead of the true placement-based value). The fix
uses page.get_image_rects(xref) from PyMuPDF to get the actual
rendered bounds. An image placed multiple times on the same page
at different sizes now emits one CodexImage record per placement,
each with the correct effective_resolution_dpi.

New CodexImage fields:
- placed_width_pts: float | None — rendered width in points
- placed_height_pts: float | None — rendered height in points
- bbox_effective: now populated from get_image_rects() rect

schema_version unchanged (additive new optional fields).

1.15.0 (minor): Root-cause fix for ``dieline.count == 0`` mismatch.
When the bbox-based dieline detector (``_estimate_dieline_size``'s
geometry-fallback path) produces a real ``dieline.size`` with
``source="analysis_stroke_bbox"`` but no named candidate hit any
of the registry-driven paths (OCG name, processing step, trap
layer, analysis signal), codex now synthesises a placeholder
candidate so ``dieline.count == 1`` and ``dieline.candidates``
is non-empty. Without this, demos showed
``Detected dieline size 4.98 x 6.53 in`` alongside
``Dieline candidates: 0`` / ``No dieline-style layers detected``
— confusing nonsense.

Additive schema additions on
``CodexSummaryDielineCandidate``:

- ``source`` literal gains ``"analysis_stroke_bbox"``.
- ``reason_codes`` literal gains ``"geometry_fallback_size_detected"``.

Top-level schema_version unchanged at 1.3.0 — consumers must
treat the Literal union as forward-compatible (open enum) per
the policies-doc forward-compatibility rule.

1.14.0 (minor): AI Signal Campaign — Phase 2 (operational
contract). Per-tenant entitlements for the AI signal lane.

Two new operator knobs:

- ``CODEX_AI_TENANTS_ALLOWLIST`` — comma-separated tenant slugs.
  When set, ONLY these tenants run AI; everyone else gets
  ``ai_tenant_excluded``. Useful for piloting AI on a single
  customer before rolling it out fleet-wide.
- ``CODEX_AI_TENANTS_DENYLIST`` — block specific tenants. Allowlist
  wins when both are set.

New warning code: ``ai_tenant_excluded``. Schema unchanged at
1.3.0.

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

VERSION = "1.17.0"
__version__ = VERSION
