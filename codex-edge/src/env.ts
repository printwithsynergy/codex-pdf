/**
 * Worker environment bindings. Mirrors the `[vars]` block in
 * wrangler.toml and the KV namespace bound as `CACHE`.
 */
export interface Env {
  CACHE: KVNamespace;
  CODEX_ORIGIN_URL: string;
  CODEX_VERSION: string;
  PROBE_TTL: string;
  PHASE1_TTL: string;
  PHASE2_TTL: string;
}
