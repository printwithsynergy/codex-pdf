import { describe, expect, it } from "vitest";

import { CodexClientError, HttpClient } from "./index.js";

describe("HttpClient", () => {
  it("requires baseUrl from options or env", () => {
    expect(() => new HttpClient({})).toThrow(CodexClientError);
  });

  it("sends auth headers", async () => {
    let captured: Headers | undefined;
    const fakeFetch: typeof fetch = async (_url, init) => {
      captured = new Headers(init?.headers);
      return new Response(JSON.stringify({ status: "ok", version: "1.2.0", ghostscript: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const client = new HttpClient({
      baseUrl: "http://codex.local",
      bearerToken: "tok",
      apiKey: "key",
      internalToken: "int",
      fetch: fakeFetch,
    });
    await client.healthz();
    expect(captured?.get("authorization")).toBe("Bearer tok");
    expect(captured?.get("x-codex-key")).toBe("key");
    expect(captured?.get("x-codex-internal")).toBe("int");
  });

  it("retries 5xx then returns body", async () => {
    let calls = 0;
    const fakeFetch: typeof fetch = async () => {
      calls += 1;
      if (calls < 2) {
        return new Response("transient", { status: 503 });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const client = new HttpClient({
      baseUrl: "http://codex.local",
      maxRetries: 3,
      fetch: fakeFetch,
    });
    const out = (await client.extract(new Uint8Array([0x25, 0x50, 0x44, 0x46]))) as { ok: boolean };
    expect(out.ok).toBe(true);
    expect(calls).toBe(2);
  });

  it("throws on persistent 4xx without retry", async () => {
    let calls = 0;
    const fakeFetch: typeof fetch = async () => {
      calls += 1;
      return new Response("bad request", { status: 400 });
    };
    const client = new HttpClient({
      baseUrl: "http://codex.local",
      fetch: fakeFetch,
    });
    await expect(client.extract(new Uint8Array())).rejects.toBeInstanceOf(CodexClientError);
    expect(calls).toBe(1);
  });

  it("parses heatmap header runs", async () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);
    const fakeFetch: typeof fetch = async () =>
      new Response(png, {
        status: 200,
        headers: {
          "Content-Type": "image/png",
          "X-Codex-Tac-Runs": JSON.stringify([
            { x0: 0, y0: 0, x1: 10, y1: 10, mean_tac: 100, limit: 300, exceeds: false },
          ]),
        },
      });
    const client = new HttpClient({ baseUrl: "http://codex.local", fetch: fakeFetch });
    const result = await client.renderHeatmap(new Uint8Array(), { page: 1 });
    expect(result.png).toEqual(png);
    expect(result.runs).toHaveLength(1);
    expect(result.runs[0]?.mean_tac).toBe(100);
  });
});
