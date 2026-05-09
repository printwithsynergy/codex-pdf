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
 * Response from ``extract()`` — the parsed CodexDocument plus the
 * sha256 the server cached the PDF under, for hash-only follow-ups.
 *
 * @public
 */
export interface ExtractResponse {
  readonly pdf_sha256: string;
  readonly [key: string]: unknown;
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
  readonly targets: { baseUrl: string; plant?: string }[];
  readonly plant?: string;
  readonly routeMode: "single" | "plant" | "failover" | "hybrid";
  readonly affinityKey?: string;
  readonly requiredSectionVersions: Record<string, string>;
  readonly bearerToken?: string;
  readonly apiKey?: string;
  readonly internalToken?: string;
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
    return { ...out, ...extra };
  }

  private async post(
    path: string,
    body: BodyInit,
    accept = "application/json",
    contentType?: string,
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
          const headers: Record<string, string> = this.headers(target, requestId, { Accept: accept });
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
    if (
      typeof pdf === "object" &&
      pdf !== null &&
      "sha256" in pdf &&
      typeof (pdf as { sha256?: unknown }).sha256 === "string"
    ) {
      fd.set("pdf_sha256", (pdf as { sha256: string }).sha256);
      return fd;
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

  async extract(pdf: ArrayBuffer | Uint8Array | Blob): Promise<ExtractResponse> {
    const fd = this.buildForm(pdf, {});
    const res = await this.post("/v1/extract", fd);
    return (await res.json()) as ExtractResponse;
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
