/**
 * `@printwithsynergy/codex-client`
 *
 * TypeScript client for the codex-pdf HTTP API. Same surface as
 * `codex_pdf.client.HttpClient` (Python). Browser-first: uses the
 * platform `fetch` and `FormData` so it works in Node 18+, Bun,
 * Deno, and modern browsers without a network polyfill.
 *
 * Configuration is environment-driven for parity with the Python
 * client, but you can also pass an explicit `CodexClientOptions`
 * object. The TypeScript client does **not** support a local
 * fallback — there is no in-process Python rendering surface in JS,
 * so callers without a `CODEX_API_BASE` get a thrown error early.
 *
 * @public
 */

export {
  alternatePantoneKey,
  cmykToSrgbNaive,
  hashHueRgb,
  labD50ToSrgb,
  normalizePantoneName,
  srgbDecode,
} from "./color.js";
export type { CmykQuad as ColorCmykQuad, LabTriplet as ColorLabTriplet, RgbTriplet as ColorRgbTriplet } from "./color.js";

export interface CodexClientOptions {
  /** Base URL of the codex API, e.g. `https://codex.example.com`. */
  baseUrl?: string;
  /** Optional endpoint pool for multi-instance routing. */
  baseUrls?: string[];
  /** Optional plant/instance identifier (hybrid routing preference). */
  plant?: string;
  /** single | plant | failover | hybrid (default when pool >1). */
  routeMode?: "single" | "plant" | "failover" | "hybrid";
  /** Deterministic affinity key for stable target selection. */
  affinityKey?: string;
  /** Required section schema versions used to preflight failover targets. */
  requiredSectionVersions?: Record<string, string>;
  bearerToken?: string;
  apiKey?: string;
  internalToken?: string;
  /**
   * Optional tenant identifier. Surfaces on every request as the
   * `X-Codex-Tenant` header so a multi-tenant codex deployment
   * scopes cache + blob store by caller. Server normalises invalid
   * values to `"default"`. Reads `CODEX_TENANT` env when omitted.
   */
  tenant?: string;
  /** Request timeout in milliseconds. Default 60000. */
  timeoutMs?: number;
  /** Number of retry attempts on transient failures. Default 3. */
  maxRetries?: number;
  /** Optional fetch implementation. Defaults to globalThis.fetch. */
  fetch?: typeof fetch;
}

export class CodexClientError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(message: string, opts: { status?: number; body?: string } = {}) {
    super(message);
    this.name = "CodexClientError";
    this.status = opts.status ?? -1;
    this.body = opts.body ?? "";
  }
}

/**
 * Reference to a PDF input. Either raw bytes (uploads on every call)
 * or ``{ sha256 }`` to reuse a previously-uploaded PDF in the codex
 * server's blob cache. Hash refs avoid re-uploading the file on
 * every render call — pass the ``pdf_sha256`` returned by
 * ``extract()`` to subsequent ``renderPage``, ``renderSeparations``,
 * etc.
 *
 * The server returns ``412 Precondition Failed`` if the hash isn't
 * in the cache (e.g. expired). Callers should catch that and retry
 * with the raw bytes.
 *
 * @public
 */
export type PdfRef =
  | ArrayBuffer
  | Uint8Array
  | Blob
  | { readonly sha256: string };

/**
 * First event from `probeStream` — bare-minimum facts, target
 * latency <50 ms cold / <10 ms warm.
 *
 * @public
 */
export interface ProbeMinEvent {
  readonly probe_phase: 1;
  readonly pdf_sha256: string;
  readonly page_count: number;
  readonly first_page_dims: { width_pts: number; height_pts: number; rotation: number } | null;
  readonly encrypted: boolean;
}

/**
 * Second event from `probeStream` — full page-dim list + info subset,
 * target latency <150 ms.
 *
 * @public
 */
export interface ProbeStdEvent {
  readonly probe_phase: 2;
  readonly pdf_sha256: string;
  readonly page_count: number;
  readonly page_dims: ReadonlyArray<{ width_pts: number; height_pts: number; rotation: number }>;
  readonly info: Record<string, string>;
  readonly pdf_version: string;
  readonly encrypted: boolean;
}

/**
 * Callbacks for `extractStream`. All are optional. When `granular`
 * is true the server splits Phase 2 into four named events; otherwise
 * only `onPhase1` and `onPhase2` fire.
 *
 * @public
 */
export interface ExtractStreamCallbacks {
  granular?: boolean;
  onPhase1?: (doc: ExtractResponse) => void;
  onPhase2?: (doc: ExtractResponse) => void;
  onColorWorld?: (data: Record<string, unknown>) => void;
  onOcgs?: (data: Record<string, unknown>) => void;
  onFormXObjects?: (data: Record<string, unknown>) => void;
  onAnalysis?: (data: Record<string, unknown>) => void;
}

/**
 * Response from ``extract()`` — the parsed CodexDocument plus the
 * sha256 the server cached the PDF under, for hash-only follow-ups.
 *
 * @public
 */
/** Canonical finding shape — consistent across all ecosystem products. */
export interface CodexFinding {
  id: string;
  type: string;
  severity: "error" | "warning" | "advisory" | "info";
  /** 1-indexed page number. */
  page: number;
  /** [x0, y0, x1, y1] in PDF points (origin bottom-left). Null for doc-level findings. */
  bbox: [number, number, number, number] | null;
  message: string;
  code?: string | null;
  data?: Record<string, unknown>;
}

/** Dieline size metrics with origin position for overlay placement. */
export interface CodexSummaryDielineSize {
  available: boolean;
  x0_pt?: number | null;
  y0_pt?: number | null;
  width_pt?: number | null;
  height_pt?: number | null;
  width_mm?: number | null;
  height_mm?: number | null;
  width_in?: number | null;
  height_in?: number | null;
  source?: string;
  confidence?: number;
  provenance?: string[];
}

export interface ExtractResponse {
  readonly pdf_sha256: string;
  /** §16.3: PDF linearization flag (fast web view). */
  readonly is_linearized?: boolean;
  /** §16 Phase C: pre-rendered page 1 at 150 DPI, keyed by render spec. */
  readonly pre_rendered?: Record<string, string>;
  /** Canonical findings from all extractors (low DPI, annotations, AI signals, dieline). */
  readonly findings?: CodexFinding[];
  readonly [key: string]: unknown;
}

/**
 * Sparse-projection field name. Any ``CodexDocument`` top-level key or
 * page sub-field name is accepted. ``"spot_colors"`` is an alias for
 * ``"color_spaces"``. Added in 1.18.0.
 *
 * @public
 */
export type CodexField =
  | "pdf_version" | "is_encrypted" | "is_linearized" | "conformance" | "info"
  | "xmp" | "trapped_flag" | "trap_evidence" | "pages" | "fonts" | "images"
  | "annotations" | "output_intents" | "color_spaces" | "spot_colors"
  | "icc_profiles" | "ocgs" | "form_xobjects" | "analysis" | "summary"
  | "findings"
  | "document_classification" | "detected_language" | "detected_barcodes"
  | "detected_logos" | "detected_symbols" | "spell_candidates"
  | "trap_zone_candidates" | "inventory" | "transparency_tree"
  | (string & Record<never, never>);  // allow unknown future fields

/**
 * Options for ``extract()``. Added in 1.18.0.
 *
 * @public
 */
export interface ExtractOptions {
  /**
   * Request only these fields. Codex runs only the extractors required
   * and returns only the listed fields plus always-included metadata
   * (``document_id``, ``pdf_sha256``, ``extraction_warnings``, …).
   *
   * Omit to receive the full document (default behaviour).
   */
  fields?: CodexField[];
}

// ---------------------------------------------------------------------------
// Unified extraction contract (1.9.0). Mirrors the Python surface
// (CodexDetectedTextRegion, CodexConformanceVerdict, CodexClauseFailure).
// Consumer-agnostic by design — no field, header, or shape assumes a
// specific caller.
//
// Cache-key contract (stable across versions):
//   text-regions: (pdf_hash, page_index, dpi)
//   conformance:  (pdf_hash, profile)
//   render:       (pdf_hash, page_index, dpi, color_space)
// ---------------------------------------------------------------------------

/**
 * Conformance profile key. Forward-compatible — consumers must treat
 * unknown values as opaque so a future codex release can add new
 * profiles (e.g. pdfx6, pdfa4, pdfua2) without breaking older clients.
 *
 * @public
 */
export type ConformanceProfile =
  | "pdfx4"
  | "pdfx1a"
  | "pdfx3"
  | "pdfa1b"
  | "pdfa2b"
  | "pdfa3b"
  | "pdfua1";

/** One text region detected on a page during extraction. Geometry in PDF points. */
export interface DetectedTextRegion {
  bbox: [number, number, number, number];
  text: string;
  confidence: number;
  polygon: ReadonlyArray<[number, number]>;
  source: string;
}

export interface TextRegionsResponse {
  pdf_hash: string;
  page_index: number;
  dpi: number;
  regions: DetectedTextRegion[];
  stage_durations_ms: Record<string, number>;
}

/** One failed conformance clause inside a ConformanceVerdictResponse. */
export interface ClauseFailure {
  clause: string;
  test_number: string;
  description: string;
  failed_check_count: number;
}

export interface ConformanceVerdictResponse {
  document_id: string;
  profile: string;
  passed: boolean;
  clauses: ClauseFailure[];
  stage_durations_ms: Record<string, number>;
}

/** One already-cached render tuple. */
export interface RenderEntry {
  page_index: number;
  dpi: number;
  color_space: string;
}

export interface RendersListResponse {
  pdf_hash: string;
  renders: RenderEntry[];
  stage_durations_ms: Record<string, number>;
}

export interface ColorSample {
  x: number;
  y: number;
  dpi: number;
  rgb: [number, number, number];
  hex: string;
}

export interface DensitometerChannel {
  name: string;
  percent: number;
}

export interface DensitometerSample {
  x: number;
  y: number;
  dpi: number;
  channels: DensitometerChannel[];
  tac: number;
  tac_limit: number;
  limit_exceeded: boolean;
}

export interface SeparationChannel {
  name: string;
  type: "process" | "spot" | "rgb" | "gray";
  png_b64: string;
}

export interface SeparationsResult {
  page_num: number;
  dpi: number;
  channels: SeparationChannel[];
}

export interface HeatmapRun {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  mean_tac: number;
  limit: number;
  exceeds: boolean;
}

export interface HeatmapResult {
  /** RGBA PNG bytes. */
  png: Uint8Array;
  runs: HeatmapRun[];
}

export interface RenderPageOptions {
  page?: number;
  dpi?: number;
  ocgOn?: number[];
  ocgOff?: number[];
  simulateOverprint?: boolean;
}

// ---------------------------------------------------------------------------
// Color authority types — mirror codex_pdf.color (Python) byte-for-byte.
// ---------------------------------------------------------------------------

export type LabTriplet = [number, number, number];
export type CmykQuad = [number, number, number, number];
export type RgbTriplet = [number, number, number];

export type SpotSwatchSource = "host" | "codex" | "pantone" | "curated" | "hash";

export interface SpotInkOverride {
  rgb?: RgbTriplet;
  lab?: LabTriplet;
  cmyk?: CmykQuad;
  pantone_name?: string;
}

export interface CodexSpotIntent {
  rgb?: RgbTriplet;
  lab?: LabTriplet;
  cmyk?: CmykQuad;
  pantone_name?: string;
}

export interface SpotSwatchResolution {
  schema_version: string;
  rgb: RgbTriplet;
  source: SpotSwatchSource;
  lab?: LabTriplet;
  cmyk?: CmykQuad;
  pantone_name?: string;
}

/** §16.1: Spot colorant as returned in CodexDocument.spot_colorants. */
export interface CodexSpotColorant {
  name: string;
  type?: string;
  rgb?: RgbTriplet;
  lab?: LabTriplet;
  cmyk?: CmykQuad;
  pantone_name?: string;
  /** Neutral density computed from Lab (§16.1). */
  neutral_density?: number | null;
  neutral_density_source?: "measured" | "computed_from_lab" | "estimated" | null;
  [key: string]: unknown;
}

export interface NeutralDensityInput {
  /** Spot ink name (resolved via spot-color ladder). */
  name?: string;
  /** CIE Lab D50 triple. */
  lab?: LabTriplet;
  /** CMYK quad (0–100). */
  cmyk?: CmykQuad;
}

export interface NeutralDensityResult {
  schema_version: string;
  neutral_density: number;
  source: "measured" | "computed_from_lab" | "estimated";
}

export interface ResolveSpotInput {
  name: string;
  hostOverride?: SpotInkOverride;
  codex?: CodexSpotIntent;
  extraPantoneOverrides?: Record<string, Record<string, unknown>>;
}

export interface MatchPantoneInput {
  lab?: LabTriplet;
  cmyk?: CmykQuad;
  rgb?: RgbTriplet;
  libraries?: string[];
}

export interface MatchPantoneResult {
  schema_version: string;
  pantone_name: string;
  library: string | null;
  delta_e: number;
  lab: LabTriplet;
  cmyk: CmykQuad | null;
  rgb: RgbTriplet;
}

export interface InkbookManifest {
  source: string;
  license: string;
  last_updated: string;
  available_libraries: string[];
  included_libraries: string[];
  included_count: number;
  total_count: number;
}

export interface InkbookPantoneEntry {
  name: string;
  library: string | null;
  lab?: LabTriplet;
  cmyk_bridge?: CmykQuad;
  lab_source?: string | null;
  cmyk_source?: string | null;
}

export interface InkbookCuratedEntry {
  rgb: RgbTriplet;
  tokens: string[];
}

export interface Inkbook {
  schema_version: string;
  manifest: InkbookManifest;
  pantone: InkbookPantoneEntry[];
  curated: InkbookCuratedEntry[];
}

// ---------------------------------------------------------------------------
// Geometry primitives — mirror codex_pdf.geom (Python). Pure data; no
// PDF-emit producer code in TS.
// ---------------------------------------------------------------------------

export interface GeomBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface GeomMarksZone {
  top?: number;
  right?: number;
  bottom?: number;
  left?: number;
}

export interface GeomTileInput {
  sheet: GeomBox;
  cellWidth: number;
  cellHeight: number;
  gutterX?: number;
  gutterY?: number;
  marksZone?: GeomMarksZone;
  origin?: "bottom-left" | "top-left";
  /** §16.2: Uniform rotation applied to every cell (degrees). */
  cellRotation?: number;
  /** §16.2: Per-cell rotation pattern (rows × cols). Overrides cellRotation. */
  cellRotationPattern?: number[][];
  /** §16.2: Alternate flip_h on every odd row. */
  flipPerRow?: boolean;
  /** §16.2: Per-cell flip_h pattern (rows × cols). Overrides flipPerRow. */
  flipPattern?: boolean[][];
  /** §16.2: How to handle bleed regions: "none" | "trim" | "extend". */
  bleedHandling?: "none" | "trim" | "extend";
  /** §16.2: Bleed amount in points. */
  bleed?: number;
}

/** §16.2: A single tile cell with placement attributes. */
export interface CellPlacement {
  box: [number, number, number, number];
  rotation: number;
  flip_h: boolean;
  flip_v: boolean;
  row: number;
  col: number;
}

export interface GeomTileResult {
  schema_version: string;
  rows: number;
  cols: number;
  /** Backward-compatible flat [x0,y0,x1,y1] boxes. */
  cells: [number, number, number, number][];
  /** §16.2: Full placement data with rotation/flip/row/col. */
  placements: CellPlacement[];
  used: [number, number, number, number];
  waste: [number, number, number, number];
}

export interface GeomOffsetInput {
  path: GeomPath;
  /** Offset distance in points. Positive = spread, negative = choke. */
  distancePt: number;
  joinType?: "miter" | "round" | "square";
  endType?: "polygon" | "joined_round" | "joined_square" | "butt" | "square" | "round";
  miterLimit?: number;
}

export interface GeomOffsetResult {
  schema_version: string;
  rings: [number, number][][];
}

export type Polygon = [number, number][];
export type GeomPath = Polygon[];

export interface GeomBooleanInput {
  subjects: GeomPath[];
  clips?: GeomPath[];
}

export interface GeomBooleanResult {
  schema_version: string;
  rings: Polygon[];
}

function envVar(name: string): string | undefined {
  if (typeof process !== "undefined" && process.env) {
    return process.env[name];
  }
  return undefined;
}

function envBool(name: string, dflt = false): boolean {
  const raw = envVar(name);
  if (!raw) return dflt;
  return /^(1|true|yes|on)$/i.test(raw.trim());
}

const DEFAULT_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_RETRIES = 3;

interface SseEvent {
  event: string; // "" when the server omitted the event: line
  data: unknown;
}

/**
 * Async-iterate `data:`/`event:` SSE frames from a Response body.
 * Only the JSON `data:` payload is surfaced — comments (`:`), `id:`
 * and `retry:` lines are ignored. Multi-line `data:` is concatenated
 * with newlines per the SSE spec.
 */
async function* parseSseStream(res: Response): AsyncGenerator<SseEvent> {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, nl);
      buf = buf.slice(nl + 2);
      let event = "";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (dataLines.length === 0) continue;
      const raw = dataLines.join("\n");
      try {
        yield { event, data: JSON.parse(raw) };
      } catch {
        // Skip malformed JSON frames silently.
      }
    }
  }
}

/**
 * Codex client. Construct once and reuse. Each method either
 * resolves with the parsed response or throws `CodexClientError` on
 * non-2xx after retries.
 *
 * @public
 */
export class HttpClient {
  readonly baseUrl: string;
  readonly targets: { baseUrl: string; plant?: string }[];
  readonly plant?: string;
  readonly routeMode: "single" | "plant" | "failover" | "hybrid";
  readonly affinityKey?: string;
  readonly requiredSectionVersions: Record<string, string>;
  readonly bearerToken?: string;
  readonly apiKey?: string;
  readonly internalToken?: string;
  readonly tenant?: string;
  readonly timeoutMs: number;
  readonly maxRetries: number;
  private readonly fetchImpl: typeof fetch;
  private readonly contractCache: Map<string, Record<string, unknown>>;

  constructor(opts: CodexClientOptions = {}) {
    const targets = this.loadTargets(opts);
    if (targets.length === 0) {
      throw new CodexClientError(
        "CODEX_API_BASE is not configured. The TypeScript codex client requires HTTP mode.",
      );
    }
    this.targets = targets;
    this.baseUrl = targets[0]!.baseUrl;
    this.plant = opts.plant ?? envVar("CODEX_PLANT") ?? undefined;
    this.routeMode =
      opts.routeMode ??
      ((envVar("CODEX_ROUTE_MODE") as "single" | "plant" | "failover" | "hybrid" | undefined) ??
        (targets.length > 1 ? "hybrid" : "single"));
    this.affinityKey =
      opts.affinityKey ?? envVar("CODEX_AFFINITY_KEY") ?? envVar("CODEX_PLANT_AFFINITY_KEY") ?? undefined;
    this.requiredSectionVersions =
      opts.requiredSectionVersions ?? this.loadRequiredSectionVersions();
    this.bearerToken = opts.bearerToken ?? envVar("CODEX_BEARER_TOKEN");
    this.apiKey = opts.apiKey ?? envVar("CODEX_API_KEY");
    this.internalToken = opts.internalToken ?? envVar("CODEX_INTERNAL_TOKEN");
    this.tenant = opts.tenant ?? envVar("CODEX_TENANT") ?? undefined;
    const envTimeout = envVar("CODEX_TIMEOUT_MS");
    this.timeoutMs =
      opts.timeoutMs ?? (envTimeout ? Number.parseInt(envTimeout, 10) : DEFAULT_TIMEOUT_MS);
    this.maxRetries = opts.maxRetries ?? DEFAULT_MAX_RETRIES;
    const fetchImpl = opts.fetch ?? (globalThis.fetch as typeof fetch | undefined);
    if (!fetchImpl) {
      throw new CodexClientError(
        "globalThis.fetch is unavailable; pass `fetch` in CodexClientOptions or upgrade to Node 18+.",
      );
    }
    this.fetchImpl = fetchImpl;
    this.contractCache = new Map();
  }

  private headers(
    target: { baseUrl: string; plant?: string } | null,
    requestId: string,
    extra: Record<string, string> = {},
  ): Record<string, string> {
    const out: Record<string, string> = {};
    if (this.bearerToken) out["Authorization"] = `Bearer ${this.bearerToken}`;
    if (this.apiKey) out["X-Codex-Key"] = this.apiKey;
    if (this.internalToken) out["X-Codex-Internal"] = this.internalToken;
    out["X-Codex-Route-Mode"] = this.routeMode;
    out["X-Codex-Request-Id"] = requestId;
    if (this.affinityKey) out["X-Codex-Affinity-Key"] = this.affinityKey;
    const effectivePlant = this.plant ?? target?.plant;
    if (effectivePlant) out["X-Codex-Plant"] = effectivePlant;
    if (this.tenant) out["X-Codex-Tenant"] = this.tenant;
    return { ...out, ...extra };
  }

  private async post(
    path: string,
    body: BodyInit,
    accept = "application/json",
    contentType?: string,
    extraHeaders?: Record<string, string>,
  ): Promise<Response> {
    let lastErr: unknown;
    const requestId = this.newRequestId();
    for (const target of this.orderedTargets()) {
      try {
        await this.ensureContractCompatible(target, requestId);
      } catch (err) {
        lastErr = err;
        continue;
      }
      for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        try {
          const headers: Record<string, string> = this.headers(target, requestId, { Accept: accept, ...extraHeaders });
          if (contentType) headers["Content-Type"] = contentType;
          const res = await this.fetchImpl(target.baseUrl + path, {
            method: "POST",
            headers,
            body,
            signal: controller.signal,
          });
          clearTimeout(timer);
          if (res.ok) return res;
          if (res.status === 408 || res.status === 429 || (res.status >= 500 && res.status < 600)) {
            lastErr = new CodexClientError(`codex ${path} -> ${res.status}`, {
              status: res.status,
            });
            // 429 carries Retry-After (seconds); honour it over the
            // exponential backoff so the server's quota math drives
            // the wait.
            let waitMs = Math.min(2 ** attempt * 1000, 8000);
            if (res.status === 429) {
              const retryAfter = res.headers.get("Retry-After");
              if (retryAfter) {
                const seconds = Number.parseFloat(retryAfter);
                if (Number.isFinite(seconds) && seconds >= 0) {
                  waitMs = Math.min(seconds * 1000, 60_000);
                }
              }
            }
            await new Promise((r) => setTimeout(r, waitMs));
            continue;
          }
          const text = await res.text().catch(() => "");
          throw new CodexClientError(`codex ${path} -> ${res.status}: ${text.slice(0, 1000)}`, {
            status: res.status,
            body: text,
          });
        } catch (err) {
          clearTimeout(timer);
          lastErr = err;
          if (err instanceof CodexClientError && err.status >= 0 && err.status < 500) {
            throw err;
          }
          await new Promise((r) => setTimeout(r, Math.min(2 ** attempt * 1000, 8000)));
        }
      }
    }
    if (lastErr instanceof Error) throw lastErr;
    throw new CodexClientError(`codex ${path} failed after retries`);
  }

  private async get(path: string): Promise<Response> {
    const requestId = this.newRequestId();
    let lastErr: unknown;
    for (const target of this.orderedTargets()) {
      try {
        await this.ensureContractCompatible(target, requestId);
      } catch (err) {
        lastErr = err;
        continue;
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const res = await this.fetchImpl(target.baseUrl + path, {
          method: "GET",
          headers: this.headers(target, requestId),
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          if (res.status === 408 || res.status === 429 || (res.status >= 500 && res.status < 600)) {
            lastErr = new CodexClientError(`codex ${path} -> ${res.status}`, {
              status: res.status,
              body: text,
            });
            continue;
          }
          throw new CodexClientError(`codex ${path} -> ${res.status}: ${text.slice(0, 1000)}`, {
            status: res.status,
            body: text,
          });
        }
        return res;
      } finally {
        clearTimeout(timer);
      }
    }
    if (lastErr instanceof Error) throw lastErr;
    throw new CodexClientError(`codex ${path} failed across all targets`);
  }

  private buildForm(
    pdf: PdfRef,
    fields: Record<string, unknown> = {},
    filename = "input.pdf",
  ): FormData {
    const fd = new FormData();
    for (const [k, v] of Object.entries(fields)) {
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) {
        fd.set(k, v.map((x) => String(x)).join(","));
      } else if (typeof v === "boolean") {
        fd.set(k, v ? "true" : "false");
      } else {
        fd.set(k, String(v));
      }
    }
    let blob: Blob;
    if (pdf instanceof Blob) {
      blob = pdf;
    } else if (pdf instanceof Uint8Array) {
      // Re-wrap into a fresh Uint8Array so the underlying buffer is
      // contiguous regardless of slice offsets.
      const ab = new ArrayBuffer(pdf.byteLength);
      new Uint8Array(ab).set(pdf);
      blob = new Blob([ab], { type: "application/pdf" });
    } else if (pdf instanceof ArrayBuffer) {
      blob = new Blob([pdf], { type: "application/pdf" });
    } else {
      // Hash-only ref — server resolves bytes from its blob cache.
      fd.set("pdf_sha256", pdf.sha256);
      return fd;
    }
    fd.set("pdf", blob, filename);
    return fd;
  }

  // ----------------------- meta ---------------------------------

  async healthz(): Promise<{
    status: string;
    version: string;
    ghostscript: boolean;
    cache_backend: string;
    instance_id?: string | null;
  }> {
    const res = await this.get("/v1/healthz");
    return (await res.json()) as {
      status: string;
      version: string;
      ghostscript: boolean;
      cache_backend: string;
      instance_id?: string | null;
    };
  }

  async version(): Promise<string> {
    const res = await this.get("/v1/version");
    const body = (await res.json()) as { version: string };
    return body.version;
  }

  async contract(): Promise<{
    contract_name: string;
    schema_version: string;
    package_version: string;
    schema_id: string;
    endpoints: string[];
  }> {
    const res = await this.get("/v1/contract");
    return (await res.json()) as {
      contract_name: string;
      schema_version: string;
      package_version: string;
      schema_id: string;
      endpoints: string[];
    };
  }

  private loadTargets(opts: CodexClientOptions): { baseUrl: string; plant?: string }[] {
    const targets: { baseUrl: string; plant?: string }[] = [];
    if (opts.baseUrl) {
      targets.push({ baseUrl: opts.baseUrl.replace(/\/+$/, "") });
      return targets;
    }
    if (opts.baseUrls && opts.baseUrls.length > 0) {
      for (const url of opts.baseUrls) {
        if (typeof url === "string" && url.trim()) {
          targets.push({ baseUrl: url.trim().replace(/\/+$/, "") });
        }
      }
      if (targets.length > 0) return targets;
    }
    const poolJson = envVar("CODEX_API_POOL_JSON");
    if (poolJson) {
      try {
        const parsed = JSON.parse(poolJson);
        if (Array.isArray(parsed)) {
          for (const item of parsed) {
            if (item && typeof item === "object") {
              const raw = (item as { base_url?: string; url?: string }).base_url ?? (item as { url?: string }).url;
              if (typeof raw === "string" && raw.trim()) {
                const plant = (item as { plant?: string }).plant;
                targets.push({
                  baseUrl: raw.trim().replace(/\/+$/, ""),
                  plant: typeof plant === "string" && plant.trim() ? plant.trim() : undefined,
                });
              }
            }
          }
        } else if (parsed && typeof parsed === "object") {
          for (const [plant, raw] of Object.entries(parsed as Record<string, unknown>)) {
            if (typeof raw === "string" && raw.trim()) {
              targets.push({ baseUrl: raw.trim().replace(/\/+$/, ""), plant });
            }
          }
        }
      } catch {
        // ignore malformed pool JSON; fallback to CODEX_API_BASE(S)
      }
      if (targets.length > 0) return targets;
    }
    const baseList = envVar("CODEX_API_BASES");
    if (baseList) {
      for (const token of baseList.split(",")) {
        const item = token.trim();
        if (!item) continue;
        if (item.includes("=")) {
          const [plant, raw] = item.split("=", 2);
          if (raw?.trim()) {
            const plantName = (plant ?? "").trim();
            targets.push({
              baseUrl: raw.trim().replace(/\/+$/, ""),
              plant: plantName || undefined,
            });
          }
        } else {
          targets.push({ baseUrl: item.replace(/\/+$/, "") });
        }
      }
      if (targets.length > 0) return targets;
    }
    const envBase = envVar("CODEX_API_BASE");
    if (envBase && envBase.trim()) {
      targets.push({ baseUrl: envBase.trim().replace(/\/+$/, "") });
    }
    return targets;
  }

  private loadRequiredSectionVersions(): Record<string, string> {
    const raw = envVar("CODEX_REQUIRED_SECTION_VERSIONS");
    if (!raw) return {};
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return {};
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof k === "string" && typeof v === "string" && k && v) out[k] = v;
      }
      return out;
    } catch {
      return {};
    }
  }

  private orderedTargets(): { baseUrl: string; plant?: string }[] {
    if (this.targets.length <= 1 || this.routeMode === "single") return [this.targets[0]!];
    let preferred = this.targets.filter((t) => this.plant && t.plant === this.plant);
    let others = this.targets.filter((t) => !this.plant || t.plant !== this.plant);
    if (preferred.length === 0) {
      preferred = [this.targets[0]!];
      others = this.targets.slice(1);
    }
    const orderedPreferred = this.rotateByAffinity(preferred);
    const orderedOthers = this.rotateByAffinity(others);
    if (this.routeMode === "plant") return orderedPreferred;
    const merged = [...orderedPreferred];
    for (const target of orderedOthers) {
      if (!merged.some((m) => m.baseUrl === target.baseUrl)) merged.push(target);
    }
    return merged;
  }

  private rotateByAffinity(targets: { baseUrl: string; plant?: string }[]): {
    baseUrl: string;
    plant?: string;
  }[] {
    if (targets.length <= 1 || !this.affinityKey) return [...targets];
    const key = this.affinityKey;
    let hash = 2166136261;
    for (let i = 0; i < key.length; i += 1) {
      hash ^= key.charCodeAt(i);
      hash = Math.imul(hash, 16777619) >>> 0;
    }
    const offset = hash % targets.length;
    return [...targets.slice(offset), ...targets.slice(0, offset)];
  }

  private newRequestId(): string {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  private async ensureContractCompatible(
    target: { baseUrl: string; plant?: string },
    requestId: string,
  ): Promise<void> {
    if (Object.keys(this.requiredSectionVersions).length === 0) return;
    const cached = this.contractCache.get(target.baseUrl);
    let payload = cached;
    if (!payload) {
      const res = await this.fetchImpl(target.baseUrl + "/v1/contract", {
        method: "GET",
        headers: this.headers(target, requestId),
      });
      if (!res.ok) {
        throw new CodexClientError(`failed to read /v1/contract from ${target.baseUrl}`, {
          status: res.status,
        });
      }
      payload = (await res.json()) as Record<string, unknown>;
      this.contractCache.set(target.baseUrl, payload);
    }
    const sections = payload.section_schema_versions as Record<string, unknown> | undefined;
    for (const [name, required] of Object.entries(this.requiredSectionVersions)) {
      const got = sections?.[name];
      if (typeof got !== "string" || got !== required) {
        throw new CodexClientError(
          `${target.baseUrl} incompatible section schema '${name}': required ${required}, got ${String(got)}`,
          { status: -1 },
        );
      }
    }
  }

  async schema(name: string): Promise<unknown> {
    const res = await this.get(`/v1/schema/${encodeURIComponent(name)}`);
    return (await res.json()) as unknown;
  }

  // ----------------------- extract ------------------------------

  async extract(pdf: ArrayBuffer | Uint8Array | Blob, options: ExtractOptions = {}): Promise<ExtractResponse> {
    const fd = this.buildForm(pdf, {});
    const extraHeaders: Record<string, string> = {};
    if (options.fields && options.fields.length > 0) {
      extraHeaders["X-Codex-Fields"] = options.fields.join(", ");
    }
    const res = await this.post("/v1/extract", fd, "application/json", undefined, extraHeaders);
    const header = res.headers.get("X-Codex-Stage-Durations-Ms");
    const body = (await res.json()) as ExtractResponse;
    if (header && body && typeof body === "object" && !("stage_durations_ms" in body)) {
      try {
        (body as Record<string, unknown>).stage_durations_ms = JSON.parse(header);
      } catch {
        // Header malformed; envelope field is the source of truth anyway.
      }
    }
    return body;
  }

  // -------------- unified extraction: per-resource (1.9.0) -----------

  /**
   * Fetch already-cached text regions for one page of a previously-
   * extracted PDF. Geometry is returned in PDF user-space points.
   *
   * Cache key: `(pdf_hash, page_index, dpi)`. Idempotent: the same
   * key returns the same bytes.
   *
   * @public
   */
  async getTextRegions(
    pdfHash: string,
    options: { pageIndex?: number; dpi?: number } = {},
  ): Promise<TextRegionsResponse> {
    const pageIndex = options.pageIndex ?? 0;
    const dpi = options.dpi ?? 150;
    const qs = `?page_index=${pageIndex}&dpi=${dpi}`;
    const res = await this.get(`/v1/documents/${encodeURIComponent(pdfHash)}/text-regions${qs}`);
    return (await res.json()) as TextRegionsResponse;
  }

  /**
   * Compute (or fetch from cache) a conformance verdict for the given
   * profile. Idempotent: a second call with the same key returns the
   * cached verdict bit-for-bit.
   *
   * Cache key: `(pdf_hash, profile)`. Forward-compatible profile enum
   * (`pdfx4`, `pdfx1a`, `pdfx3`, `pdfa1b`, `pdfa2b`, `pdfa3b`,
   * `pdfua1`) — consumers must treat unknown values as opaque.
   *
   * @public
   */
  async computeConformance(
    documentId: string,
    profile: ConformanceProfile,
  ): Promise<ConformanceVerdictResponse> {
    const path = `/v1/documents/${encodeURIComponent(documentId)}/conformance/${encodeURIComponent(profile)}`;
    const res = await this.post(path, "", "application/json");
    return (await res.json()) as ConformanceVerdictResponse;
  }

  /**
   * List `(page_index, dpi, color_space)` tuples that are already in
   * the render cache for this PDF so consumers can skip re-requests.
   *
   * Render cache key: `(pdf_hash, page_index, dpi, color_space)`.
   *
   * @public
   */
  async listRenders(pdfHash: string): Promise<RendersListResponse> {
    const res = await this.get(`/v1/documents/${encodeURIComponent(pdfHash)}/renders`);
    return (await res.json()) as RendersListResponse;
  }

  /**
   * Two-event probe SSE stream — page count + dims arrive in <50 ms,
   * full page-dim list + info in <150 ms.
   *
   * Both callbacks fire at most once. Resolves when the server closes
   * the stream. PDF can be raw bytes or `{ sha256 }` to reuse a
   * server-side blob.
   *
   * @public
   */
  async probeStream(
    pdf: PdfRef,
    callbacks: {
      onMin?: (event: ProbeMinEvent) => void;
      onStd?: (event: ProbeStdEvent) => void;
    } = {},
  ): Promise<void> {
    const init = this.buildSseRequest(pdf);
    const res = await this.post("/v1/probe", init.body, "text/event-stream", init.contentType);
    for await (const ev of parseSseStream(res)) {
      const data = ev.data as { probe_phase?: number };
      if (data?.probe_phase === 1) callbacks.onMin?.(data as ProbeMinEvent);
      else if (data?.probe_phase === 2) callbacks.onStd?.(data as ProbeStdEvent);
    }
  }

  /**
   * Streaming extract. Default mode emits Phase 1 then Phase 2 (full
   * doc) — equivalent to the old behaviour. With `granular: true` the
   * server emits five named events: `phase1`, `color_world`, `ocgs`,
   * `form_xobjects`, `analysis`, `phase2_complete` (in completion
   * order for the four pikepdf passes).
   *
   * Resolves with the merged final document on success.
   *
   * @public
   */
  async extractStream(
    pdf: PdfRef,
    callbacks: ExtractStreamCallbacks = {},
  ): Promise<ExtractResponse> {
    const granular = callbacks.granular === true;
    const path = granular ? "/v1/extract/stream?granular=1" : "/v1/extract/stream";
    const init = this.buildSseRequest(pdf);
    const res = await this.post(path, init.body, "text/event-stream", init.contentType);

    let final: ExtractResponse | undefined;
    for await (const ev of parseSseStream(res)) {
      const data = ev.data as Record<string, unknown>;
      if (granular) {
        switch (ev.event) {
          case "phase1":
            callbacks.onPhase1?.(data as ExtractResponse);
            break;
          case "color_world":
            callbacks.onColorWorld?.(data);
            break;
          case "ocgs":
            callbacks.onOcgs?.(data);
            break;
          case "form_xobjects":
            callbacks.onFormXObjects?.(data);
            break;
          case "analysis":
            callbacks.onAnalysis?.(data);
            break;
          case "phase2_complete":
            final = data as ExtractResponse;
            callbacks.onPhase2?.(final);
            break;
          default:
            break;
        }
      } else {
        const phase = (data as { extract_phase?: number }).extract_phase;
        if (phase === 1) {
          callbacks.onPhase1?.(data as ExtractResponse);
        } else if (phase === 2) {
          final = data as ExtractResponse;
          callbacks.onPhase2?.(final);
        }
      }
    }
    if (!final) {
      throw new CodexClientError("extract stream ended without phase2_complete", {
        status: -1,
      });
    }
    return final;
  }

  /**
   * Build a body for SSE endpoints. Multipart upload when given raw
   * bytes; JSON `{pdf_sha256}` when given a hash ref. The probe and
   * extract-stream endpoints both accept either form.
   */
  private buildSseRequest(pdf: PdfRef): { body: BodyInit; contentType?: string } {
    if (
      typeof (pdf as { sha256?: unknown }).sha256 === "string"
    ) {
      return {
        body: JSON.stringify({ pdf_sha256: (pdf as { sha256: string }).sha256 }),
        contentType: "application/json",
      };
    }
    return { body: this.buildForm(pdf, {}) };
  }

  // ----------------------- render -------------------------------

  async renderPage(
    pdf: PdfRef,
    opts: RenderPageOptions = {},
  ): Promise<Uint8Array> {
    const fd = this.buildForm(pdf, {
      page: opts.page ?? 1,
      dpi: opts.dpi ?? 300,
      ocg_on: opts.ocgOn ?? [],
      ocg_off: opts.ocgOff ?? [],
      simulate_overprint: opts.simulateOverprint !== false,
    });
    const res = await this.post("/v1/render/page", fd, "image/png");
    return new Uint8Array(await res.arrayBuffer());
  }

  async renderSeparations(
    pdf: PdfRef,
    opts: { page?: number; dpi?: number } = {},
  ): Promise<SeparationsResult> {
    const fd = this.buildForm(pdf, {
      page: opts.page ?? 1,
      dpi: opts.dpi ?? 150,
    });
    const res = await this.post("/v1/render/separations", fd);
    return (await res.json()) as SeparationsResult;
  }

  async renderHeatmap(
    pdf: PdfRef,
    opts: { page?: number; dpi?: number; tacLimit?: number } = {},
  ): Promise<HeatmapResult> {
    const fd = this.buildForm(pdf, {
      page: opts.page ?? 1,
      dpi: opts.dpi ?? 150,
      tac_limit: opts.tacLimit ?? 300,
    });
    const res = await this.post("/v1/render/heatmap", fd, "image/png");
    const png = new Uint8Array(await res.arrayBuffer());
    const runsHeader = res.headers.get("X-Codex-Tac-Runs") ?? "[]";
    let runs: HeatmapRun[] = [];
    try {
      runs = JSON.parse(runsHeader) as HeatmapRun[];
    } catch {
      runs = [];
    }
    return { png, runs };
  }

  async renderLayer(
    pdf: PdfRef,
    opts: {
      page?: number;
      layerIndex: number;
      allLayerIndices: number[];
      dpi?: number;
    },
  ): Promise<Uint8Array> {
    const fd = this.buildForm(pdf, {
      page: opts.page ?? 1,
      layer_index: opts.layerIndex,
      all_layer_indices: opts.allLayerIndices,
      dpi: opts.dpi ?? 150,
    });
    const res = await this.post("/v1/render/layer", fd, "image/png");
    return new Uint8Array(await res.arrayBuffer());
  }

  // ----------------------- sample -------------------------------

  async sampleColor(
    pdf: PdfRef,
    opts: {
      page?: number;
      x: number;
      y: number;
      pageW?: number;
      pageH?: number;
      dpi?: number;
    },
  ): Promise<ColorSample> {
    const fields: Record<string, unknown> = {
      page: opts.page ?? 1,
      x: opts.x,
      y: opts.y,
      dpi: opts.dpi ?? 300,
    };
    if (opts.pageW !== undefined) fields.page_w = opts.pageW;
    if (opts.pageH !== undefined) fields.page_h = opts.pageH;
    const fd = this.buildForm(pdf, fields);
    const res = await this.post("/v1/sample/color", fd);
    return (await res.json()) as ColorSample;
  }

  async sampleDensity(
    pdf: PdfRef,
    opts: {
      page?: number;
      x: number;
      y: number;
      pageW?: number;
      pageH?: number;
      dpi?: number;
      tacLimit?: number;
    },
  ): Promise<DensitometerSample> {
    const fields: Record<string, unknown> = {
      page: opts.page ?? 1,
      x: opts.x,
      y: opts.y,
      dpi: opts.dpi ?? 300,
      tac_limit: opts.tacLimit ?? 300,
    };
    if (opts.pageW !== undefined) fields.page_w = opts.pageW;
    if (opts.pageH !== undefined) fields.page_h = opts.pageH;
    const fd = this.buildForm(pdf, fields);
    const res = await this.post("/v1/sample/density", fd);
    return (await res.json()) as DensitometerSample;
  }

  async walkContentStream(
    pdf: PdfRef,
    opts: { page?: number } = {},
  ): Promise<{ page_num: number; signals: Record<string, unknown> }> {
    const fd = this.buildForm(pdf, { page: opts.page ?? 1 });
    const res = await this.post("/v1/walk/content-stream", fd);
    return (await res.json()) as { page_num: number; signals: Record<string, unknown> };
  }

  /**
   * Evaluate a PDF Type-4 PostScript function via codex.
   *
   * Returns `{result, fast_path}`. `result` is `null` when codex
   * could not verify (Ghostscript missing, timeout, parse error).
   * `fast_path: true` indicates the program was constant and was
   * resolved without a subprocess (sub-millisecond).
   */
  async evalType4(
    program: string,
    inputs: number[] = [],
  ): Promise<{ result: number[] | null; fast_path: boolean }> {
    const body = JSON.stringify({ program, inputs });
    const res = await this.post("/v1/walk/type4", body, "application/json", "application/json");
    return (await res.json()) as { result: number[] | null; fast_path: boolean };
  }

  // ----------------------- color (1.4.0+) -----------------------

  /**
   * Resolve a spot ink to a display swatch + provenance.
   *
   * Mirrors :func:`codex_pdf.color.resolve_spot_swatch_color` —
   * host → codex → pantone → curated → hash precedence ladder.
   */
  async resolveSpotColor(input: ResolveSpotInput): Promise<SpotSwatchResolution> {
    const body = JSON.stringify({
      name: input.name,
      host_override: input.hostOverride,
      codex: input.codex,
      extra_pantone_overrides: input.extraPantoneOverrides,
    });
    const res = await this.post("/v1/color/resolve", body, "application/json", "application/json");
    return (await res.json()) as SpotSwatchResolution;
  }

  /**
   * Find the nearest Pantone reference to a Lab/RGB/CMYK measurement.
   *
   * The codex server uses ΔE2000 to rank candidates against the
   * requested library filter (defaults to Formula Guide Coated +
   * Uncoated; pass `libraries: ["*"]` for the full catalogue).
   */
  async matchPantone(input: MatchPantoneInput): Promise<MatchPantoneResult> {
    const body = JSON.stringify({
      lab: input.lab,
      cmyk: input.cmyk,
      rgb: input.rgb,
      libraries: input.libraries,
    });
    const res = await this.post(
      "/v1/color/match-pantone",
      body,
      "application/json",
      "application/json",
    );
    return (await res.json()) as MatchPantoneResult;
  }

  /**
   * §16.1: Compute neutral density for a spot colorant.
   *
   * Accepts a spot name (resolved via the spot-color ladder), a Lab
   * D50 triple, or a CMYK quad. Lab is most accurate; CMYK uses
   * naïve linearisation.
   */
  async neutralDensity(input: NeutralDensityInput): Promise<NeutralDensityResult> {
    const body = JSON.stringify({
      name: input.name,
      lab: input.lab,
      cmyk: input.cmyk,
    });
    const res = await this.post(
      "/v1/color/neutral-density",
      body,
      "application/json",
      "application/json",
    );
    return (await res.json()) as NeutralDensityResult;
  }

  /**
   * Fetch the bundled inkbook (curated + Pantone catalogue).
   */
  async getInkbook(libraries?: string[]): Promise<Inkbook> {
    const qs =
      libraries && libraries.length > 0
        ? `?libraries=${encodeURIComponent(libraries.join(","))}`
        : "";
    const res = await this.get(`/v1/color/inkbook${qs}`);
    return (await res.json()) as Inkbook;
  }

  // ----------------------- geom (1.4.0+) ------------------------

  /**
   * Compute an imposition tile-grid layout (read-only / pure data).
   */
  async geomTile(input: GeomTileInput): Promise<GeomTileResult> {
    const body = JSON.stringify({
      sheet: input.sheet,
      cell_width: input.cellWidth,
      cell_height: input.cellHeight,
      gutter_x: input.gutterX ?? 0,
      gutter_y: input.gutterY ?? 0,
      marks_zone: {
        top: input.marksZone?.top ?? 0,
        right: input.marksZone?.right ?? 0,
        bottom: input.marksZone?.bottom ?? 0,
        left: input.marksZone?.left ?? 0,
      },
      origin: input.origin ?? "bottom-left",
      cell_rotation: input.cellRotation ?? 0,
      cell_rotation_pattern: input.cellRotationPattern ?? null,
      flip_per_row: input.flipPerRow ?? false,
      flip_pattern: input.flipPattern ?? null,
      bleed_handling: input.bleedHandling ?? "none",
      bleed: input.bleed ?? 0,
    });
    const res = await this.post("/v1/geom/tile", body, "application/json", "application/json");
    return (await res.json()) as GeomTileResult;
  }

  /**
   * §16.2: Offset (spread/choke) a polygon path.
   *
   * Positive `distancePt` spreads (grows); negative chokes (shrinks).
   * Uses pyclipr (Clipper2) on the server when available; rectangle
   * fast-path otherwise.
   */
  async geomOffset(input: GeomOffsetInput): Promise<GeomOffsetResult> {
    const body = JSON.stringify({
      path: input.path,
      distance_pt: input.distancePt,
      join_type: input.joinType ?? "miter",
      end_type: input.endType ?? "polygon",
      miter_limit: input.miterLimit ?? 2.0,
    });
    const res = await this.post("/v1/geom/offset", body, "application/json", "application/json");
    return (await res.json()) as GeomOffsetResult;
  }

  async geomIntersect(input: GeomBooleanInput): Promise<GeomBooleanResult> {
    return this.geomBoolean("intersect", input);
  }

  async geomUnion(input: GeomBooleanInput): Promise<GeomBooleanResult> {
    return this.geomBoolean("union", input);
  }

  async geomDifference(input: GeomBooleanInput): Promise<GeomBooleanResult> {
    return this.geomBoolean("difference", input);
  }

  private async geomBoolean(
    op: "intersect" | "union" | "difference",
    input: GeomBooleanInput,
  ): Promise<GeomBooleanResult> {
    const body = JSON.stringify({
      subjects: input.subjects,
      clips: input.clips,
    });
    const res = await this.post(`/v1/geom/${op}`, body, "application/json", "application/json");
    return (await res.json()) as GeomBooleanResult;
  }
}
