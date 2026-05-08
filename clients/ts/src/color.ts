/**
 * Browser-side colour-math helpers that mirror
 * :mod:`codex_pdf.color.color_math` byte-for-byte. Used by loupe-pdf's
 * `resolveSpotSwatchColor` adapter so chip swatches don't require a
 * network round-trip when host/codex/curated paths already have
 * enough information.
 *
 * @public
 */

export type LabTriplet = [number, number, number];
export type CmykQuad = [number, number, number, number];
export type RgbTriplet = [number, number, number];

const XYZ_D65_FROM_LINEAR_SRGB = [
  [0.4124564, 0.3575761, 0.1804375],
  [0.2126729, 0.7151522, 0.0721750],
  [0.0193339, 0.1191920, 0.9503041],
] as const;

const LINEAR_SRGB_FROM_XYZ_D65 = [
  [3.2404542, -1.5371385, -0.4985314],
  [-0.9692660, 1.8760108, 0.0415560],
  [0.0556434, -0.2040259, 1.0572252],
] as const;

const D50_TO_D65 = [
  [0.9555766, -0.0230393, 0.0631636],
  [-0.0282895, 1.0099416, 0.0210077],
  [0.0122982, -0.0204830, 1.3299098],
] as const;

const D50_WHITE: readonly [number, number, number] = [0.9642, 1.0, 0.8249];

function matMul3(
  triple: readonly [number, number, number],
  matrix: readonly (readonly [number, number, number])[],
): [number, number, number] {
  const [a, b, c] = triple;
  return [
    matrix[0]![0] * a + matrix[0]![1] * b + matrix[0]![2] * c,
    matrix[1]![0] * a + matrix[1]![1] * b + matrix[1]![2] * c,
    matrix[2]![0] * a + matrix[2]![1] * b + matrix[2]![2] * c,
  ];
}

function srgbEncode(linear: number): number {
  const v = Math.max(0, Math.min(1, linear));
  if (v <= 0.0031308) return 12.92 * v;
  return 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
}

export function srgbDecode(channel: number): number {
  const v = Math.max(0, Math.min(1, channel));
  if (v <= 0.04045) return v / 12.92;
  return Math.pow((v + 0.055) / 1.055, 2.4);
}

function clamp255(v: number): number {
  return Math.max(0, Math.min(255, Math.round(v)));
}

/**
 * Convert CIE Lab (D50, 2° observer) to an sRGB triplet on [0, 255].
 * Out-of-gamut values are clamped per channel.
 */
export function labD50ToSrgb([L, a, b]: LabTriplet): RgbTriplet {
  const fy = (L + 16) / 116;
  const fx = a / 500 + fy;
  const fz = fy - b / 200;
  const eps = 216 / 24389;
  const kappa = 24389 / 27;
  const fxC = fx * fx * fx;
  const fzC = fz * fz * fz;
  const xr = fxC > eps ? fxC : (116 * fx - 16) / kappa;
  const yr = L > kappa * eps ? Math.pow((L + 16) / 116, 3) : L / kappa;
  const zr = fzC > eps ? fzC : (116 * fz - 16) / kappa;
  const x50 = xr * D50_WHITE[0];
  const y50 = yr * D50_WHITE[1];
  const z50 = zr * D50_WHITE[2];
  const [X, Y, Z] = matMul3([x50, y50, z50] as const, D50_TO_D65);
  const [lr, lg, lb] = matMul3([X, Y, Z] as const, LINEAR_SRGB_FROM_XYZ_D65);
  return [
    clamp255(srgbEncode(lr) * 255),
    clamp255(srgbEncode(lg) * 255),
    clamp255(srgbEncode(lb) * 255),
  ];
}

/**
 * Convert CMYK (channels in 0–1 or 0–100, auto-detected) to a naïve
 * subtractive sRGB triplet on [0, 255]. Approximation only — preserve
 * for swatch chips, never press readouts.
 */
export function cmykToSrgbNaive([c, m, y, k]: CmykQuad): RgbTriplet {
  const isPercent = c > 1 || m > 1 || y > 1 || k > 1;
  const div = isPercent ? 100 : 1;
  const cn = c / div;
  const mn = m / div;
  const yn = y / div;
  const kn = k / div;
  const r = (1 - cn) * (1 - kn);
  const g = (1 - mn) * (1 - kn);
  const b = (1 - yn) * (1 - kn);
  return [clamp255(r * 255), clamp255(g * 255), clamp255(b * 255)];
}

const SPACE_COLLAPSE = /\s+/g;
const SUFFIX_WITH_SPACE = /^(PANTONE\s+.+?)\s+([CUMV])$/;
const SUFFIX_NO_SPACE = /^(PANTONE\s+.+\S)([CUMV])$/;
const PMS_PREFIX = /^\s*(?:PMS|P\.M\.S\.|P\s*M\s*S)\s+/i;

/**
 * Canonicalise a Pantone-style name to UPPERCASE + collapsed spaces.
 * Mirrors :func:`codex_pdf.color.normalize_pantone_name` (Python).
 */
export function normalizePantoneName(name: string): string {
  return name.trim().replace(PMS_PREFIX, "PANTONE ").toUpperCase().replace(SPACE_COLLAPSE, " ");
}

/**
 * Try alternate spacing around a trailing finish suffix (C/U/M/V).
 * Returns null when the input doesn't carry a recognised suffix.
 */
export function alternatePantoneKey(key: string): string | null {
  const ws = SUFFIX_WITH_SPACE.exec(key);
  if (ws) return `${ws[1]}${ws[2]}`;
  const ns = SUFFIX_NO_SPACE.exec(key);
  if (ns) return `${ns[1]} ${ns[2]}`;
  return null;
}

/**
 * Stable hash-of-name → HSL → sRGB. Identical to the Python
 * resolver's `hash_hue_rgb` so existing visual identities don't
 * shuffle between the in-process and HTTP paths.
 */
export function hashHueRgb(name: string): RgbTriplet {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (name.charCodeAt(i) + ((hash << 5) - hash)) | 0;
  }
  const hue = Math.abs(hash) % 360;
  const s = 0.7;
  const l = 0.45;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (hue < 60) {
    r = c;
    g = x;
  } else if (hue < 120) {
    r = x;
    g = c;
  } else if (hue < 180) {
    g = c;
    b = x;
  } else if (hue < 240) {
    g = x;
    b = c;
  } else if (hue < 300) {
    r = x;
    b = c;
  } else {
    r = c;
    b = x;
  }
  return [
    Math.max(0, Math.min(255, Math.round((r + m) * 255))),
    Math.max(0, Math.min(255, Math.round((g + m) * 255))),
    Math.max(0, Math.min(255, Math.round((b + m) * 255))),
  ];
}
