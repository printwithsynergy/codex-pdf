/**
 * Port of `codex_pdf.api.cache.cache_key` so edge KV keys are
 * byte-identical to the Redis keys the origin writes. Sharing the
 * format means `VERSION` bumps invalidate both tiers at once.
 *
 * Format: `codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}` where
 * `args_sha = sha256(JSON.stringify(args, sortedKeys, compact))`.
 *
 * For probe + Phase 1 cache lookups the args dict is empty, so
 * `args_sha` is the sha256 of `{}` — but we still compute it so the
 * key matches the origin exactly.
 */

export async function sha256Hex(input: ArrayBuffer | string): Promise<string> {
  const buf = typeof input === "string" ? new TextEncoder().encode(input) : new Uint8Array(input);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return hexlify(new Uint8Array(digest));
}

function hexlify(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * Stable JSON encoding for `args`. Mirrors the Python side:
 * `json.dumps(args, sort_keys=True, separators=(",", ":"))`.
 */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return "[" + value.map(stableStringify).join(",") + "]";
  }
  const keys = Object.keys(value as Record<string, unknown>).sort();
  const obj = value as Record<string, unknown>;
  return (
    "{" +
    keys
      .map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k]))
      .join(",") +
    "}"
  );
}

export async function cacheKey(
  pdfSha: string,
  args: Record<string, unknown>,
  kind: string,
  version: string,
): Promise<string> {
  const argsSha = await sha256Hex(stableStringify(args));
  return `codex:${version}:${kind}:${pdfSha}:${argsSha}`;
}

export async function shaOfBytes(bytes: ArrayBuffer): Promise<string> {
  return sha256Hex(bytes);
}
