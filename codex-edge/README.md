# codex-edge

Cloudflare Worker that puts the codex-pdf probe + extract endpoints on
the Cloudflare global edge. KV-backed write-through cache. Origin is
the Railway codex-pdf service.

## What it caches

| Endpoint                        | Cache key kind         | TTL    |
| ------------------------------- | ---------------------- | ------ |
| `POST /v1/probe` event 1        | `probe-min`            | 24 h   |
| `POST /v1/probe` event 2        | `probe-std`            | 24 h   |
| `POST /v1/extract/stream` p1    | `extract-phase-1`      | 24 h   |
| `POST /v1/extract/stream` p2    | `extract`              | 7 d    |
| Granular `phase1`               | `extract-phase-1-min`  | 24 h   |
| Granular `phase2_complete`      | `extract`              | 7 d    |

Keys are byte-identical to the origin's Redis keys
(`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`), so bumping
`codex_pdf.version.VERSION` invalidates both tiers atomically.

## Caching scope

- Hash-keyed JSON requests (`{"pdf_sha256": "..."}`) hit edge cache.
- Multipart uploads bypass the edge and proxy straight to origin —
  hashing the upload at the edge would add latency without a hit
  chance on the cold path.

## Local dev

```sh
npm install
npm run dev   # wrangler dev — local Worker against staging KV
npm test
npm run typecheck
```

## Deploy

`main` push deploys to production via the GH Action in
`.github/workflows/deploy.yml`. Required secrets:

- `CLOUDFLARE_API_TOKEN` — scoped to Workers Scripts: Edit + KV: Edit
- `CLOUDFLARE_ACCOUNT_ID`

## Operations

- The Worker is **fail-open**: any KV / Worker error proxies the
  request to origin transparently.
- `GET /edge/healthz` reports origin RTT and the deployed
  `CODEX_VERSION` so probes can confirm the version-skew between
  edge and origin.
- Anything not on the cached path forwards to origin untouched, so
  the Worker is a drop-in DNS replacement for the Railway URL.
