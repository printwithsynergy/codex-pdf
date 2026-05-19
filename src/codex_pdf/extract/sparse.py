"""Sparse field projection for /v1/extract.

When the caller sets ``X-Codex-Fields`` the server runs only the
extractors required to fill the requested fields and returns only
those fields in the response, shrinking both latency and payload size.

Field names are the top-level ``CodexDocument`` JSON keys **plus** a
handful of page-level sub-field aliases (e.g. ``detected_barcodes``
which lives inside each page object).

Usage (HTTP)::

    POST /v1/extract
    X-Codex-Fields: detected_barcodes, color_spaces

The server maps those names to extractor groups, runs only the required
extractors, and returns a filtered ``CodexDocument`` containing just the
requested fields plus always-included metadata.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Extractor group name constants.
# ---------------------------------------------------------------------------

GRP_FITZ_STRUCTURE = "fitz_structure"
GRP_FITZ_FONTS = "fitz_fonts"
GRP_FITZ_IMAGES = "fitz_images"
GRP_FITZ_ANNOTATIONS = "fitz_annotations"
GRP_FITZ_INVENTORY = "fitz_inventory"
GRP_FITZ_TRANSPARENCY = "fitz_transparency"
GRP_PIKEPDF_COLOR = "pikepdf_color"
GRP_PIKEPDF_OCGS = "pikepdf_ocgs"
GRP_PIKEPDF_FORMS = "pikepdf_forms"
GRP_PIKEPDF_SIGNALS = "pikepdf_signals"
GRP_AI_CLASSIFICATION = "ai_classification"
GRP_AI_LANGUAGE = "ai_language"
GRP_AI_BARCODES = "ai_barcodes"
GRP_AI_LOGOS = "ai_logos"
GRP_AI_SYMBOLS = "ai_symbols"
GRP_AI_SPELL = "ai_spell"
GRP_AI_TRAP_ZONES = "ai_trap_zones"

# ---------------------------------------------------------------------------
# Field → extractor groups mapping.
#
# Each key is a field name callers can request. The value is the set of
# extractor groups that must run to populate that field. An empty set means
# the field is derived from whatever is already collected (e.g. "summary").
# ---------------------------------------------------------------------------

FIELD_EXTRACTOR_GROUPS: dict[str, set[str]] = {
    # ── Core document metadata (structure pass always runs) ──────────────
    "pdf_version":          {GRP_FITZ_STRUCTURE},
    "is_encrypted":         {GRP_FITZ_STRUCTURE},
    "is_linearized":        {GRP_FITZ_STRUCTURE},
    "conformance":          {GRP_FITZ_STRUCTURE},
    "info":                 {GRP_FITZ_STRUCTURE},
    "xmp":                  {GRP_FITZ_STRUCTURE},
    "trapped_flag":         {GRP_FITZ_STRUCTURE},
    "trap_evidence":        {GRP_FITZ_STRUCTURE},
    # ── Pages (basic geometry always from structure pass) ─────────────────
    "pages":                {GRP_FITZ_STRUCTURE},
    # ── PyMuPDF sub-passes ────────────────────────────────────────────────
    "fonts":                {GRP_FITZ_FONTS},
    "images":               {GRP_FITZ_IMAGES},
    "annotations":          {GRP_FITZ_ANNOTATIONS},
    # Page sub-fields from fitz
    "inventory":            {GRP_FITZ_INVENTORY},
    "transparency_tree":    {GRP_FITZ_TRANSPARENCY},
    # ── pikepdf passes ────────────────────────────────────────────────────
    "output_intents":       {GRP_PIKEPDF_COLOR},
    "color_spaces":         {GRP_PIKEPDF_COLOR},
    "spot_colors":          {GRP_PIKEPDF_COLOR},   # alias → same extractors
    "icc_profiles":         {GRP_PIKEPDF_COLOR},
    "ocgs":                 {GRP_PIKEPDF_OCGS},
    "form_xobjects":        {GRP_PIKEPDF_FORMS},
    "analysis":             {GRP_PIKEPDF_SIGNALS},
    # ── AI signal lane ───────────────────────────────────────────────────
    "document_classification": {GRP_AI_CLASSIFICATION},
    "detected_language":       {GRP_AI_LANGUAGE},
    "detected_barcodes":       {GRP_AI_BARCODES},
    "detected_logos":          {GRP_AI_LOGOS},
    "detected_symbols":        {GRP_AI_SYMBOLS},
    "spell_candidates":        {GRP_AI_SPELL},
    "trap_zone_candidates":    {GRP_AI_TRAP_ZONES},
    # ── Derived / compound ───────────────────────────────────────────────
    "summary":              set(),  # built from whatever is already collected
}

# AI signal kind names keyed by their extractor group name.
AI_GROUP_TO_KIND: dict[str, str] = {
    GRP_AI_CLASSIFICATION: "classification",
    GRP_AI_LANGUAGE:       "language",
    GRP_AI_BARCODES:       "barcodes",
    GRP_AI_LOGOS:          "logos",
    GRP_AI_SYMBOLS:        "symbols",
    GRP_AI_SPELL:          "spell",
    GRP_AI_TRAP_ZONES:     "trap_zones",
}

# Field names that live inside page objects rather than at document top-level.
PAGE_SUBFIELDS: frozenset[str] = frozenset({
    "inventory",
    "transparency_tree",
    "detected_language",
    "detected_barcodes",
    "detected_logos",
    "detected_symbols",
    "spell_candidates",
    "trap_zone_candidates",
})

# Document-level keys always returned regardless of field filter.
_ALWAYS_INCLUDE: frozenset[str] = frozenset({
    "schema_version",
    "codex_version",
    "document_id",
    "source",
    "pdf_sha256",
    "extraction_warnings",
    "stage_durations_ms",
    "preflight_reports",
    "conformance_verdicts",
    "ai_status",
})


def parse_fields_header(header_value: str | None) -> set[str] | None:
    """Parse ``X-Codex-Fields`` header value into a set of field names.

    Returns ``None`` when the header is absent or empty (meaning "return
    all fields"). Normalises to lowercase and strips whitespace.
    """
    if not header_value:
        return None
    fields = {f.strip().lower() for f in header_value.split(",") if f.strip()}
    return fields if fields else None


def resolve_groups(fields: set[str]) -> set[str]:
    """Return the union of extractor groups needed for all *fields*."""
    groups: set[str] = set()
    for field in fields:
        groups.update(FIELD_EXTRACTOR_GROUPS.get(field, set()))
    return groups


def resolve_ai_kinds(groups: set[str]) -> set[str] | None:
    """Translate AI extractor groups into signal kind names.

    Returns ``None`` when no AI groups are present in *groups* (skip the
    AI lane entirely). Returns an empty set only when AI groups were
    requested but none mapped to a known kind — treat as "skip AI".
    """
    kinds: set[str] = set()
    any_ai = False
    for grp in groups:
        kind = AI_GROUP_TO_KIND.get(grp)
        if kind is not None:
            any_ai = True
            kinds.add(kind)
    return kinds if any_ai else None


def filter_document_payload(payload: dict, fields: set[str]) -> dict:
    """Return a copy of *payload* containing only *fields* + always-include keys.

    Pages are included when ``"pages"`` is in *fields* or when any
    page sub-field is requested. Page sub-fields are stripped to only
    those explicitly requested.

    ``"spot_colors"`` is treated as an alias for ``"color_spaces"``.
    """
    # Resolve alias so we look up the real key in the payload.
    effective = set(fields)
    if "spot_colors" in effective:
        effective.add("color_spaces")
        effective.discard("spot_colors")

    include_pages = "pages" in effective or bool(effective & PAGE_SUBFIELDS)

    result: dict = {}

    # Always-include metadata.
    for key in _ALWAYS_INCLUDE:
        if key in payload:
            result[key] = payload[key]

    # Requested document-level fields (excluding page sub-fields and pages
    # itself — pages is handled separately below).
    for field in effective:
        if field in _ALWAYS_INCLUDE:
            continue
        if field == "pages":
            continue  # handled below
        if field in PAGE_SUBFIELDS:
            continue  # handled below
        if field in payload:
            result[field] = payload[field]

    # Pages — always include core page keys; strip page sub-fields that
    # were not requested.
    if include_pages:
        pages = payload.get("pages")
        if isinstance(pages, list):
            page_subfields_wanted = effective & PAGE_SUBFIELDS
            filtered: list = []
            for page in pages:
                if not isinstance(page, dict):
                    filtered.append(page)
                    continue
                new_page = {
                    k: v for k, v in page.items()
                    if k not in PAGE_SUBFIELDS or k in page_subfields_wanted
                }
                filtered.append(new_page)
            result["pages"] = filtered

    return result
