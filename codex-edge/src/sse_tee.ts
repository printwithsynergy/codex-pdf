/**
 * SSE write-through cache. Streams the upstream response straight to
 * the client (so the client gets bytes the moment origin emits them)
 * while accumulating each `event:`/`data:` frame so we can write a
 * complete cached payload to KV when the stream ends.
 *
 * KV lookup keys are passed in as a map: event-name → KV key. The
 * default event ("" — no `event:` line) maps to a single key when the
 * caller knows the upstream emits exactly one `data:` frame.
 *
 * Errors mid-stream are not cached — only successful runs that
 * actually emit the expected events end up in KV.
 */

export interface ParsedFrame {
  event: string;
  data: string; // raw JSON text
}

export interface TeeOptions {
  kv: KVNamespace;
  /** event-name (or "" for default) → KV key, KV value is the raw JSON. */
  routes: Record<string, { key: string; ttl: number }>;
}

export function teeAndCache(
  upstream: ReadableStream<Uint8Array>,
  opts: TeeOptions,
): ReadableStream<Uint8Array> {
  const decoder = new TextDecoder("utf-8");
  const encoder = new TextEncoder();
  const reader = upstream.getReader();
  const captured: ParsedFrame[] = [];
  let buf = "";

  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { value, done } = await reader.read();
      if (done) {
        controller.close();
        // Flush captured frames to KV after the response is fully sent.
        // We don't await — KV writes are fire-and-forget; failure is
        // logged by Cloudflare and treated as a future cache miss.
        for (const frame of captured) {
          const route = opts.routes[frame.event];
          if (route) {
            opts.kv.put(route.key, frame.data, { expirationTtl: route.ttl }).catch(() => {});
          }
        }
        return;
      }
      controller.enqueue(value);
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
        if (dataLines.length > 0) {
          captured.push({ event, data: dataLines.join("\n") });
        }
      }
      // Avoid unused-var warnings on the encoder import in some build modes.
      void encoder;
    },
    cancel(reason) {
      reader.cancel(reason).catch(() => {});
    },
  });
}

export function buildSseEvent(eventName: string, data: string): string {
  if (eventName) return `event: ${eventName}\ndata: ${data}\n\n`;
  return `data: ${data}\n\n`;
}
