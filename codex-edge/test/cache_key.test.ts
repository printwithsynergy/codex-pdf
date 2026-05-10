import { describe, it, expect } from "vitest";
import { stableStringify, sha256Hex, cacheKey } from "../src/cache_key";

describe("cache_key", () => {
  it("stableStringify sorts keys at every level", () => {
    expect(stableStringify({ b: 2, a: 1 })).toBe('{"a":1,"b":2}');
    expect(stableStringify({ z: { y: 1, x: 2 } })).toBe('{"z":{"x":2,"y":1}}');
    expect(stableStringify([{ b: 2, a: 1 }])).toBe('[{"a":1,"b":2}]');
  });

  it("sha256Hex matches Python hashlib for the empty-args sentinel", async () => {
    // Python: hashlib.sha256(json.dumps({}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    //   == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    const sha = await sha256Hex(stableStringify({}));
    expect(sha).toBe("44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");
  });

  it("cacheKey matches the Python format", async () => {
    const key = await cacheKey("a".repeat(64), {}, "extract", "1.6.0");
    expect(key).toBe(
      "codex:1.6.0:extract:" +
        "a".repeat(64) +
        ":44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    );
  });
});
