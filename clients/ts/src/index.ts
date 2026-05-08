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
  bearerToken?: string;
  apiKey?: string;
  internalToken?: string;
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
}

export interface GeomTileResult {
  schema_version: string;
  rows: number;
  cols: number;
  cells: [number, number, number, number][];
  used: [number, number, number, number];
  waste: [number, number, number, number];
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

/**
 * Codex client. Construct once and reuse. Each method either
 * resolves with the parsed response or throws `CodexClientError` on
 * non-2xx after retries.
 *
 * @public
 */
export class HttpClient {
  readonly baseUrl: string;
  readonly bearerToken?: string;
  readonly apiKey?: string;
  readonly internalToken?: string;
  readonly timeoutMs: number;
  readonly maxRetries: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: CodexClientOptions = {}) {
    const baseUrl = opts.baseUrl ?? envVar("CODEX_API_BASE");
    if (!baseUrl) {
      throw new CodexClientError(
        "CODEX_API_BASE is not configured. The TypeScript codex client requires HTTP mode.",
      );
    }
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.bearerToken = opts.bearerToken ?? envVar("CODEX_BEARER_TOKEN");
    this.apiKey = opts.apiKey ?? envVar("CODEX_API_KEY");
    this.internalToken = opts.internalToken ?? envVar("CODEX_INTERNAL_TOKEN");
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
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const out: Record<string, string> = {};
    if (this.bearerToken) out["Authorization"] = `Bearer ${this.bearerToken}`;
    if (this.apiKey) out["X-Codex-Key"] = this.apiKey;
    if (this.internalToken) out["X-Codex-Internal"] = this.internalToken;
    return { ...out, ...extra };
  }

  private async post(
    path: string,
    body: BodyInit,
    accept = "application/json",
    contentType?: string,
  ): Promise<Response> {
    let lastErr: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const headers: Record<string, string> = this.headers({ Accept: accept });
        if (contentType) headers["Content-Type"] = contentType;
        // For FormData, let fetch set Content-Type itself so the
        // multipart boundary is correct; we only override for JSON
        // and other explicit body types.
        const res = await this.fetchImpl(this.baseUrl + path, {
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
          await new Promise((r) => setTimeout(r, Math.min(2 ** attempt * 1000, 8000)));
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
    if (lastErr instanceof Error) throw lastErr;
    throw new CodexClientError(`codex ${path} failed after retries`);
  }

  private async get(path: string): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetchImpl(this.baseUrl + path, {
        method: "GET",
        headers: this.headers(),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        const text = await res.text().catch(() => "");
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

  private buildForm(
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    } else {
      blob = new Blob([pdf], { type: "application/pdf" });
    }
    fd.set("pdf", blob, filename);
    return fd;
  }

  // ----------------------- meta ---------------------------------

  async healthz(): Promise<{ status: string; version: string; ghostscript: boolean }> {
    const res = await this.get("/v1/healthz");
    return (await res.json()) as { status: string; version: string; ghostscript: boolean };
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

  async schema(name: string): Promise<unknown> {
    const res = await this.get(`/v1/schema/${encodeURIComponent(name)}`);
    return (await res.json()) as unknown;
  }

  // ----------------------- extract ------------------------------

  async extract(pdf: ArrayBuffer | Uint8Array | Blob): Promise<unknown> {
    const fd = this.buildForm(pdf, {});
    const res = await this.post("/v1/extract", fd);
    return (await res.json()) as unknown;
  }

  // ----------------------- render -------------------------------

  async renderPage(
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    pdf: ArrayBuffer | Uint8Array | Blob,
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
    });
    const res = await this.post("/v1/geom/tile", body, "application/json", "application/json");
    return (await res.json()) as GeomTileResult;
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
