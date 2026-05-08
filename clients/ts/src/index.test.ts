import { describe, expect, it } from "vitest";

import {
  CodexClientError,
  HttpClient,
  alternatePantoneKey,
  hashHueRgb,
  labD50ToSrgb,
  normalizePantoneName,
} from "./index.js";

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
    expect(captured?.get("x-codex-route-mode")).toBe("single");
    expect(captured?.get("x-codex-request-id")).toBeTruthy();
  });

  it("fails over between targets in hybrid mode", async () => {
    const seen: string[] = [];
    const fakeFetch: typeof fetch = async (url) => {
      const u = String(url);
      seen.push(u);
      if (u === "https://a.example.com/v1/contract") {
        return new Response(
          JSON.stringify({
            contract_name: "codex-document",
            section_schema_versions: { color: "0.9.0", geom: "1.0.0" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (u === "https://b.example.com/v1/contract") {
        return new Response(
          JSON.stringify({
            contract_name: "codex-document",
            section_schema_versions: { color: "1.0.0", geom: "1.0.0" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (u === "https://b.example.com/v1/healthz") {
        return new Response(
          JSON.stringify({ status: "ok", version: "1.4.2", ghostscript: true }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    };
    const client = new HttpClient({
      baseUrls: ["https://a.example.com", "https://b.example.com"],
      routeMode: "hybrid",
      requiredSectionVersions: { color: "1.0.0" },
      fetch: fakeFetch,
    });
    const health = await client.healthz();
    expect(health.version).toBe("1.4.2");
    expect(seen).toContain("https://a.example.com/v1/contract");
    expect(seen).toContain("https://b.example.com/v1/contract");
    expect(seen).toContain("https://b.example.com/v1/healthz");
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

  it("normalises Pantone names and alternates", () => {
    expect(normalizePantoneName("Pantone 485 c")).toBe("PANTONE 485 C");
    expect(normalizePantoneName("PMS 485 C")).toBe("PANTONE 485 C");
    expect(alternatePantoneKey("PANTONE 485 C")).toBe("PANTONE 485C");
    expect(alternatePantoneKey("PANTONE 485C")).toBe("PANTONE 485 C");
  });

  it("converts Lab D50 white to clamped sRGB white", () => {
    expect(labD50ToSrgb([100, 0, 0])).toEqual([255, 255, 255]);
  });

  it("hash-hue is stable across calls", () => {
    expect(hashHueRgb("custom-spot")).toEqual(hashHueRgb("custom-spot"));
  });

  it("calls the codex color resolve endpoint", async () => {
    let captured: string | undefined;
    const fakeFetch: typeof fetch = async (_url, init) => {
      captured = init?.body as string | undefined;
      return new Response(
        JSON.stringify({
          schema_version: "1.0.0",
          rgb: [200, 30, 30],
          source: "pantone",
          lab: [50, 70, 30],
          cmyk: null,
          pantone_name: "PANTONE 485 C",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };
    const client = new HttpClient({ baseUrl: "http://codex.local", fetch: fakeFetch });
    const result = await client.resolveSpotColor({ name: "PANTONE 485 C" });
    expect(result.source).toBe("pantone");
    expect(result.pantone_name).toBe("PANTONE 485 C");
    expect(captured).toBe(JSON.stringify({ name: "PANTONE 485 C" }));
  });

  it("calls the codex tile endpoint", async () => {
    const fakeFetch: typeof fetch = async () =>
      new Response(
        JSON.stringify({
          schema_version: "1.0.0",
          rows: 2,
          cols: 2,
          cells: [[0, 0, 80, 80]],
          used: [0, 0, 80, 80],
          waste: [0, 0, 200, 200],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    const client = new HttpClient({ baseUrl: "http://codex.local", fetch: fakeFetch });
    const result = await client.geomTile({
      sheet: { x0: 0, y0: 0, x1: 200, y1: 200 },
      cellWidth: 80,
      cellHeight: 80,
      gutterX: 20,
      gutterY: 20,
    });
    expect(result.rows).toBe(2);
    expect(result.cells.length).toBeGreaterThan(0);
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
