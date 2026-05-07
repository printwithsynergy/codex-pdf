# `@printwithsynergy/codex-client`

TypeScript client for the [`codex-pdf`](https://pypi.org/project/codex-pdf/)
HTTP API. Mirrors `codex_pdf.client.HttpClient` (Python).

## Install

```bash
npm install @printwithsynergy/codex-client
```

## Usage

```ts
import { HttpClient } from "@printwithsynergy/codex-client";

const codex = new HttpClient({
  baseUrl: process.env.CODEX_API_BASE,
  bearerToken: process.env.CODEX_BEARER_TOKEN,
});

const png = await codex.renderPage(pdfBytes, { page: 1, dpi: 300 });
const seps = await codex.renderSeparations(pdfBytes, { page: 1 });
const sample = await codex.sampleDensity(pdfBytes, { x: 100, y: 200 });
```

The client supports `CODEX_API_BASE`, `CODEX_BEARER_TOKEN`,
`CODEX_API_KEY`, `CODEX_INTERNAL_TOKEN`, and `CODEX_TIMEOUT_MS` from
the environment when the matching options aren't passed.

There is **no** local fallback: codex byte-level work happens
server-side. Construct the client with `baseUrl` set to your codex
deployment URL and let the typed methods do the rest.
