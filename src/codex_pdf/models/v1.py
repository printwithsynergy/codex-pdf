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
    """

    name: str
    alternate_space_id: str | None = None
    tint_transform: str | None = None
    lab: tuple[float, float, float] | None = None
    cmyk: tuple[float, float, float, float] | None = None
    rgb: tuple[float, float, float] | None = None
    pantone_name: str | None = None


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
    swatch_source: Literal["rgb", "cmyk", "fallback"]
    rgb: tuple[int, int, int] | None = None
    cmyk: tuple[float, float, float, float] | None = None
    lab: tuple[float, float, float] | None = None
    pantone_name: str | None = None


class CodexSummarySpotColorMetrics(BaseModel):
    count: int = 0
    colors: list[CodexSummarySpotColor] = Field(default_factory=list)


class CodexSummaryDielineCandidate(BaseModel):
    name: str
    source: Literal["ocg_name", "ocg_processing_step", "trap_layer"]
    ocg_id: str | None = None
    processing_step: str | None = None


class CodexSummaryDielineMetrics(BaseModel):
    count: int = 0
    candidates: list[CodexSummaryDielineCandidate] = Field(default_factory=list)
    trapped_flag: Literal["True", "False", "Unknown"] | None = None


class CodexDocumentSummary(BaseModel):
    version: str = "1.0"
    counts: CodexSummaryCountMetrics = Field(default_factory=CodexSummaryCountMetrics)
    images: CodexSummaryImageMetrics = Field(default_factory=CodexSummaryImageMetrics)
    pages: CodexSummaryPageMetrics = Field(default_factory=CodexSummaryPageMetrics)
    source: CodexSummarySourceMetrics = Field(default_factory=CodexSummarySourceMetrics)
    spot_colors: CodexSummarySpotColorMetrics = Field(default_factory=CodexSummarySpotColorMetrics)
    dieline: CodexSummaryDielineMetrics = Field(default_factory=CodexSummaryDielineMetrics)


class CodexDocument(BaseModel):
    schema_version: str = "1.0.0"
    codex_version: str
    document_id: str
    source: CodexSourceRef
    pdf_version: str = "unknown"
    is_encrypted: bool = False
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
