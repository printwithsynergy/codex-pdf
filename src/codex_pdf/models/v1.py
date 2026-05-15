"""codexPDF v1 contract models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CodexSourceRef(BaseModel):
    uri: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None


class CodexConformanceClaims(BaseModel):
    pdfx: str | None = None
    pdfa: str | None = None
    pdfua: str | None = None


class CodexInfoDict(BaseModel):
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    mod_date: str | None = None
    custom: dict[str, str] = Field(default_factory=dict)


class CodexXmpPacket(BaseModel):
    present: bool = False
    pdfxid: str | None = None
    pdfa_part: str | None = None
    pdfua_part: str | None = None


class CodexWarning(BaseModel):
    code: str
    message: str
    scope: str | None = None


class CodexBBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class CodexPageBoxes(BaseModel):
    media: CodexBBox
    crop: CodexBBox | None = None
    bleed: CodexBBox | None = None
    trim: CodexBBox | None = None
    art: CodexBBox | None = None


class CodexOutputIntent(BaseModel):
    subtype: str | None = None
    output_condition_identifier: str | None = None
    profile_id: str | None = None


class CodexSpotColorant(BaseModel):
    """A spot colorant declared on a Separation / DeviceN colour space.

    Optional ``lab`` / ``cmyk`` / ``rgb`` / ``pantone_name`` carry per
    colorant colour intent so downstream renderers (e.g. the loupe-pdf
    viewer) can resolve intent-accurate swatches without falling back
    to hash-of-name pseudo-random hues. Extractors are free to leave
    them ``None`` — additive fields, no breaking change.

    ``neutral_density`` (§16.1) is the optical density of the colorant
    measured at 0% tint on the paper substrate; ``neutral_density_source``
    records how it was determined.
    """

    name: str
    alternate_space_id: str | None = None
    tint_transform: str | None = None
    lab: tuple[float, float, float] | None = None
    cmyk: tuple[float, float, float, float] | None = None
    rgb: tuple[float, float, float] | None = None
    pantone_name: str | None = None
    neutral_density: float | None = None
    neutral_density_source: Literal["measured", "computed_from_lab", "estimated"] | None = None


class CodexColorSpace(BaseModel):
    id: str
    family: Literal[
        "DeviceGray",
        "DeviceRGB",
        "DeviceCMYK",
        "ICCBased",
        "Separation",
        "DeviceN",
        "Lab",
        "CalRGB",
        "CalGray",
        "Indexed",
        "Pattern",
    ]
    canonical: dict[str, Any] = Field(default_factory=dict)
    icc_profile_id: str | None = None
    alternate_space_id: str | None = None
    tint_transform: str | None = None
    spot_colorants: list[CodexSpotColorant] = Field(default_factory=list)


class CodexICCProfile(BaseModel):
    profile_id: str
    sha256: str
    name: str | None = None
    version: Literal["v2", "v4", "unknown"] = "unknown"
    profile_class: str | None = None
    color_space: str | None = None
    pcs: str | None = None
    source: Literal["OutputIntent", "ColorSpace", "Image", "Other"] = "Other"
    named_match: str | None = None


class CodexFont(BaseModel):
    font_id: str
    base_name: str | None = None
    subtype: str = "unknown"
    encoding: str | None = None
    embedded: Literal["full", "subset", "referenced", "unknown"] = "unknown"
    outline_type: Literal["CFF", "TrueType", "Type1", "Type3", "CID", "unknown"] = "unknown"
    missing_glyphs_detected: bool = False
    page_refs: list[int] = Field(default_factory=list)


class CodexResolution(BaseModel):
    x_dpi: float
    y_dpi: float


class CodexImage(BaseModel):
    image_id: str
    page_num: int
    width_px: int
    height_px: int
    bits_per_component: int | None = None
    color_space_id: str | None = None
    compression: str | None = None
    soft_mask: bool = False
    bbox_effective: CodexBBox | None = None
    effective_resolution_dpi: CodexResolution | None = None


class CodexGraphicsStateSnapshot(BaseModel):
    fill_color_space_id: str | None = None
    stroke_color_space_id: str | None = None
    alpha_fill: float | None = None
    alpha_stroke: float | None = None
    overprint_fill: bool | None = None
    overprint_stroke: bool | None = None


class CodexColorUsage(BaseModel):
    color_space_id: str | None = None
    spot_colorants: list[str] = Field(default_factory=list)


class CodexPageObject(BaseModel):
    object_id: str
    kind: Literal["text", "vector", "raster", "shading", "form_xobject"]
    bbox_effective: CodexBBox | None = None
    clip_paths: list[str] = Field(default_factory=list)
    graphics_state: CodexGraphicsStateSnapshot = Field(default_factory=CodexGraphicsStateSnapshot)
    color_usage: CodexColorUsage = Field(default_factory=CodexColorUsage)
    blend_mode: str | None = None
    opacity_fill: float | None = None
    opacity_stroke: float | None = None
    ocg_membership: list[str] = Field(default_factory=list)


class CodexTransparencyGroup(BaseModel):
    group_id: str
    isolated: bool = False
    knockout: bool = False
    blend_mode: str | None = None


class CodexSoftMask(BaseModel):
    mask_id: str
    subtype: str | None = None


class CodexKnockoutGroup(BaseModel):
    group_id: str
    enabled: bool = True


class CodexLazySamplerDescriptor(BaseModel):
    mode: Literal["on_demand"] = "on_demand"
    endpoint: str | None = None


class CodexTransparencyTree(BaseModel):
    groups: list[CodexTransparencyGroup] = Field(default_factory=list)
    soft_masks: list[CodexSoftMask] = Field(default_factory=list)
    knockout_groups: list[CodexKnockoutGroup] = Field(default_factory=list)
    lazy_sampler: CodexLazySamplerDescriptor = Field(default_factory=CodexLazySamplerDescriptor)


class CodexOCG(BaseModel):
    ocg_id: str
    name: str
    default_visible: bool = True
    intent: list[str] = Field(default_factory=list)
    iso19593_processing_step: str | None = None


class CodexTrapLayerEvidence(BaseModel):
    ocg_id: str | None = None
    name: str | None = None
    processing_step: str | None = None


class CodexTrapEvidence(BaseModel):
    trapped_flag: Literal["True", "False", "Unknown"] | None = None
    trap_network_annotations: list[str] = Field(default_factory=list)
    trap_layers: list[CodexTrapLayerEvidence] = Field(default_factory=list)
    interpretation_notes: list[str] = Field(default_factory=list)


class CodexAnnotation(BaseModel):
    annotation_id: str
    subtype: str | None = None
    page_num: int
    rect: CodexBBox | None = None
    contents: str | None = None
    has_appearance_stream: bool = False


class CodexDetectedTextRegion(BaseModel):
    """A text region detected on a page during extraction.

    Geometry is expressed in PDF user-space points so consumers can
    composite regions with other codex outputs (boxes, inventory) without
    a per-call DPI conversion. ``polygon`` is optional: it carries a
    tighter outline when the detector produced one (e.g. layout
    analysis), while ``bbox`` is the always-present axis-aligned
    bounding box. ``source`` records which detector path emitted the
    region — extractors are free to add new source labels; consumers
    must treat unknown values as opaque.

    Cache key: ``(pdf_hash, page_index, dpi)`` — see
    ``GET /v1/documents/{pdf_hash}/text-regions``.
    """

    bbox: CodexBBox
    text: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    polygon: list[tuple[float, float]] = Field(default_factory=list)
    source: str = "unknown"


# Conformance profile keys. Forward-compatible: consumers must treat
# unknown values as opaque so a future codex release can add new
# profiles (e.g. pdfx6, pdfa4, pdfua2) without breaking older clients.
ConformanceProfile = Literal[
    "pdfx4",
    "pdfx1a",
    "pdfx3",
    "pdfa1b",
    "pdfa2b",
    "pdfa3b",
    "pdfua1",
]


class CodexClauseFailure(BaseModel):
    """One failed conformance clause inside a ``ConformanceVerdict``.

    ``clause`` and ``test_number`` reference the ISO specification text
    (e.g. clause "6.2.3.3" / test "3.4-1" for PDF/X-4). Consumers should
    treat unknown clause/test identifiers as opaque strings.
    """

    clause: str
    test_number: str
    description: str = ""
    failed_check_count: int = Field(default=0, ge=0)


class CodexConformanceVerdict(BaseModel):
    """Pass/fail verdict for one conformance profile.

    Cache key: ``(pdf_hash, profile)`` — see
    ``POST /v1/documents/{document_id}/conformance/{profile}``. Verdicts
    are idempotent: a second call for the same ``(pdf_hash, profile)``
    returns the cached verdict.
    """

    passed: bool = False
    clauses: list[CodexClauseFailure] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI signal extraction — Codex AI Signal Campaign Phase 0 (1.3.0).
#
# These are *detection signals*, not policy verdicts. Codex emits the
# raw facts (what language is this text? where is this logo? what
# barcode is encoded?); downstream consumers (lint, loupe, compile)
# apply tenant policy on top.
#
# All fields default to empty. Codex extraction populates them when
# (a) the operator has enabled AI via ``CODEX_AI_ENABLED=true`` and
# (b) the caller has not opted out via ``X-Codex-Skip-AI: true``.
#
# When AI is requested but unavailable, codex emits a ``CodexWarning``
# with ``code="ai_disabled"`` (operator-side) or ``code="ai_skipped"``
# (caller-side opt-out) so consumers can render an honest "AI signals
# not available" state instead of pretending the data was checked.
# ---------------------------------------------------------------------------


class CodexDetectedLanguage(BaseModel):
    """Detected dominant language on a page.

    Cache key: ``(pdf_hash, page_index, "language")``. ``code`` is a
    BCP-47 tag (``en``, ``en-US``, ``fr``, ``zh-Hans``).
    ``confidence`` is the detector's posterior probability.
    ``source`` records which detector emitted the signal — extractors
    are free to add new labels; consumers must treat unknown values
    as opaque.
    """

    code: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "unknown"


class CodexDetectedLogo(BaseModel):
    """Detected logo / brand mark on a page.

    Cache key: ``(pdf_hash, page_index, "logos")``. ``identity`` is
    the canonical brand name when recognised (``"FedEx"``, ``"USDA
    Organic"``) or ``None`` for unknown logos that still have a
    bbox. ``source`` records the detector path.
    """

    bbox: CodexBBox
    identity: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "unknown"


class CodexDetectedSymbol(BaseModel):
    """Detected regulatory / safety / packaging symbol.

    Cache key: ``(pdf_hash, page_index, "symbols")``. ``kind`` is a
    stable identifier like ``"ghs_flammable"``, ``"recycle_pet"``,
    ``"fda_drug_facts"``, ``"ce_marking"``. Consumers must treat
    unknown kinds as opaque (the catalogue grows additively).
    """

    bbox: CodexBBox
    kind: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "unknown"


class CodexDetectedBarcode(BaseModel):
    """One decoded barcode on a page.

    Cache key: ``(pdf_hash, page_index, "barcodes")``. ``format`` is
    one of ``"ean13"``, ``"upca"``, ``"code128"``, ``"qr"``,
    ``"datamatrix"``, ``"pdf417"``, ``"aztec"``, …; consumers treat
    unknown formats as opaque. ``value`` is the decoded payload.
    """

    bbox: CodexBBox
    format: str
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "unknown"


# Signal kind literal — used as the path component on the per-resource
# signals endpoint and as a key in any future signal aggregation. Forward
# compatible: a future codex release may add ``"images"``, ``"fonts"``,
# etc.; consumers must treat unknown values as opaque.
class CodexTrapZoneCandidate(BaseModel):
    """One detected ink-boundary candidate for trap zone generation.

    Detected by Claude vision on a rendered page raster. Downstream
    consumers (compile-pdf-trap) filter by ``confidence`` and convert
    ``polygon_pt`` into trap zone declarations. Cache key:
    ``(pdf_hash, page_index, "trap_zones")``.

    ``content_type`` describes the nature of the boundary:
    - ``"solid-solid"`` — two flat-colour regions meeting
    - ``"text-bg"`` — text object against a background colour
    - ``"image-image"`` — two image regions abutting
    - ``"image-solid"`` — image region against a flat colour
    """

    page_index: int
    polygon_pt: list[tuple[float, float]] = Field(default_factory=list)
    from_ink: str
    to_ink: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    content_type: Literal["solid-solid", "text-bg", "image-image", "image-solid"] = "solid-solid"
    source: str = "unknown"


SignalKind = Literal[
    "language",
    "logos",
    "symbols",
    "barcodes",
    "spell",
    "classification",
    "trap_zones",
]


class CodexIssue(BaseModel):
    issue_id: str
    inspection_id: str | None = None
    severity: Literal["error", "warning", "advisory"]
    message: str
    page_num: int | None = None
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CodexPreflightReport(BaseModel):
    report_id: str
    source_engine: Literal["lintPDF", "callas", "PitStop", "Acrobat", "other"]
    engine_version: str | None = None
    ingest_format: str
    issues: list[CodexIssue] = Field(default_factory=list)
    ingest_warnings: list[CodexWarning] = Field(default_factory=list)


class CodexFormXObject(BaseModel):
    object_id: str
    parent_object_id: str | None = None
    resource_refs: list[str] = Field(default_factory=list)


class CodexPageResourcesRef(BaseModel):
    font_ids: list[str] = Field(default_factory=list)
    color_space_ids: list[str] = Field(default_factory=list)
    xobject_ids: list[str] = Field(default_factory=list)


class CodexPage(BaseModel):
    page_num: int
    rotation: int = 0
    boxes: CodexPageBoxes
    resources: CodexPageResourcesRef = Field(default_factory=CodexPageResourcesRef)
    inventory: list[CodexPageObject] = Field(default_factory=list)
    transparency_tree: CodexTransparencyTree = Field(default_factory=CodexTransparencyTree)
    annotations: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    # Populated during extraction when codex has computed text regions for
    # the page; absent otherwise. On-demand re-fetch:
    # ``GET /v1/documents/{pdf_hash}/text-regions?page_index=N&dpi=N``.
    # Cache key: ``(pdf_hash, page_index, dpi)``.
    detected_text_regions: list[CodexDetectedTextRegion] = Field(default_factory=list)
    # AI signals (Codex AI Signal Campaign — Phase 0, 1.3.0).
    # Populated only when both ``CODEX_AI_ENABLED=true`` (operator) and
    # ``X-Codex-Skip-AI: false`` (caller); empty + ``CodexWarning`` in
    # ``extraction_warnings`` otherwise. See docs/policies.md.
    detected_language: CodexDetectedLanguage | None = None
    detected_logos: list[CodexDetectedLogo] = Field(default_factory=list)
    detected_symbols: list[CodexDetectedSymbol] = Field(default_factory=list)
    detected_barcodes: list[CodexDetectedBarcode] = Field(default_factory=list)
    # Pure unknown-word list (no dictionary policy). Lint applies tenant
    # spell rules on top; codex just emits the raw candidates.
    spell_candidates: list[str] = Field(default_factory=list)
    # Ink-boundary candidates for trap zone inference (Codex AI Signal).
    # Populated only when AI is enabled. Compile-pdf-trap reads these via
    # ``trap_zones_source="codex_extract"`` to seed zone generation.
    # Cache key: ``(pdf_hash, page_index, "trap_zones")``.
    trap_zone_candidates: list[CodexTrapZoneCandidate] = Field(default_factory=list)


class CodexSummaryCountMetrics(BaseModel):
    pages: int = 0
    images: int = 0
    fonts: int = 0
    embedded_fonts: int = 0
    referenced_fonts: int = 0
    fonts_with_missing_glyphs: int = 0


class CodexSummaryImageMetrics(BaseModel):
    dpi_avg: float | None = None
    dpi_min: float | None = None
    below_300_dpi: int = 0
    largest_width_px: int | None = None
    largest_height_px: int | None = None
    largest_area_px2: int | None = None


class CodexSummaryPageSize(BaseModel):
    width_in: float
    height_in: float
    width_mm: float
    height_mm: float


class CodexSummaryPageMetrics(BaseModel):
    first_page: CodexSummaryPageSize | None = None
    total_area_sq_in: float = 0.0
    total_area_sq_ft: float = 0.0
    total_area_sq_mm: float = 0.0


class CodexSummarySourceMetrics(BaseModel):
    size_bytes: int | None = None
    size_mb: float | None = None


class CodexSummarySpotColor(BaseModel):
    name: str
    swatch_hex: str
    swatch_source: Literal[
        "rgb",
        "icc_alternate",
        "cmyk",
        "lab",
        "pantone",
        "curated",
        "hash",
        "fallback",
    ]
    swatch_note: str | None = None
    rgb: tuple[int, int, int] | None = None
    cmyk: tuple[float, float, float, float] | None = None
    lab: tuple[float, float, float] | None = None
    pantone_name: str | None = None


class CodexSummarySpotColorMetrics(BaseModel):
    count: int = 0
    colors: list[CodexSummarySpotColor] = Field(default_factory=list)


class CodexSummaryDielineCandidate(BaseModel):
    name: str
    # ``analysis_stroke_bbox`` is the bbox-based geometry-only path —
    # used when none of the named-layer / trap-layer / analysis-signal
    # paths produced a candidate but ``size`` was still derivable from
    # stroked-path bboxes across pages. Consumers MUST treat the
    # Literal set as forward-compatible (open enum) so older clients
    # don't break against newer servers.
    source: Literal[
        "ocg_name",
        "ocg_processing_step",
        "trap_layer",
        "analysis_signal",
        "analysis_stroke_bbox",
    ]
    ocg_id: str | None = None
    processing_step: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_codes: list[
        Literal[
            "name_keyword",
            "iso19593_processing_step",
            "trap_layer_keyword",
            "analysis_ocg_marked_keyword",
            "analysis_dash_pattern",
            "analysis_thin_stroke",
            "analysis_stroke_dominant",
            "analysis_dense_path_network",
            "analysis_low_fill_ratio",
            "geometry_fallback_size_detected",
        ]
    ] = Field(default_factory=list)


class CodexSummaryDielineSizeMetrics(BaseModel):
    available: bool = False
    width_pt: float | None = None
    height_pt: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    width_in: float | None = None
    height_in: float | None = None
    depth_pt: float | None = None
    depth_mm: float | None = None
    depth_in: float | None = None
    depth_available: bool = False
    depth_note: str = "Unavailable from 2D PDF geometry"
    source: Literal["analysis_stroke_bbox", "unavailable"] = "unavailable"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: list[str] = Field(default_factory=list)


class CodexSummaryDielineMetrics(BaseModel):
    count: int = 0
    candidates: list[CodexSummaryDielineCandidate] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    trapped_flag: Literal["True", "False", "Unknown"] | None = None
    detector_version: str = "canonical-v1"
    size: CodexSummaryDielineSizeMetrics = Field(default_factory=CodexSummaryDielineSizeMetrics)


class CodexDocumentSummary(BaseModel):
    version: str = "1.0"
    counts: CodexSummaryCountMetrics = Field(default_factory=CodexSummaryCountMetrics)
    images: CodexSummaryImageMetrics = Field(default_factory=CodexSummaryImageMetrics)
    pages: CodexSummaryPageMetrics = Field(default_factory=CodexSummaryPageMetrics)
    source: CodexSummarySourceMetrics = Field(default_factory=CodexSummarySourceMetrics)
    spot_colors: CodexSummarySpotColorMetrics = Field(default_factory=CodexSummarySpotColorMetrics)
    dieline: CodexSummaryDielineMetrics = Field(default_factory=CodexSummaryDielineMetrics)


class CodexDocument(BaseModel):
    schema_version: str = "1.3.0"
    codex_version: str
    document_id: str
    source: CodexSourceRef
    pdf_version: str = "unknown"
    is_encrypted: bool = False
    is_linearized: bool = False
    conformance: CodexConformanceClaims = Field(default_factory=CodexConformanceClaims)
    info: CodexInfoDict | None = None
    xmp: CodexXmpPacket | None = None
    trapped_flag: Literal["True", "False", "Unknown"] | None = None
    output_intents: list[CodexOutputIntent] = Field(default_factory=list)
    icc_profiles: list[CodexICCProfile] = Field(default_factory=list)
    color_spaces: list[CodexColorSpace] = Field(default_factory=list)
    fonts: list[CodexFont] = Field(default_factory=list)
    images: list[CodexImage] = Field(default_factory=list)
    ocgs: list[CodexOCG] = Field(default_factory=list)
    pages: list[CodexPage] = Field(default_factory=list)
    form_xobjects: list[CodexFormXObject] = Field(default_factory=list)
    trap_evidence: CodexTrapEvidence = Field(default_factory=CodexTrapEvidence)
    annotations: list[CodexAnnotation] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    summary: CodexDocumentSummary | None = None
    preflight_reports: list[CodexPreflightReport] = Field(default_factory=list)
    extraction_warnings: list[CodexWarning] = Field(default_factory=list)
    # Empty until a verdict has been requested via
    # ``POST /v1/documents/{document_id}/conformance/{profile}``. Keys
    # are :data:`ConformanceProfile` literals; consumers must treat
    # unknown keys as opaque so new profiles are additive.
    # Cache key: ``(pdf_hash, profile)``.
    conformance_verdicts: dict[ConformanceProfile, CodexConformanceVerdict] = Field(
        default_factory=dict
    )
    # Per-stage wall-clock telemetry in milliseconds. The same dict is
    # emitted as the ``X-Codex-Stage-Durations-Ms`` response header so
    # transports that strip headers (in-process clients, mocks) still
    # surface it. Initial stage names: ``extract``, ``render``,
    # ``text_regions``, ``conformance``. Adding new ones is
    # non-breaking — consumers treat unknown keys as opaque.
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    # Document classification probabilities (Codex AI Signal Campaign).
    # Keys are stable category strings (``"prescription_drug"``,
    # ``"otc_drug"``, ``"food_packaging"``, ``"folding_carton"``,
    # ``"sign"``, ``"proof"``, …); values are confidence probabilities
    # ``[0.0, 1.0]``. Empty when AI is disabled / skipped — check
    # ``extraction_warnings`` for ``code="ai_disabled"`` or
    # ``code="ai_skipped"`` to disambiguate. Cache key:
    # ``(pdf_hash, "classification")``.
    document_classification: dict[str, float] = Field(default_factory=dict)
