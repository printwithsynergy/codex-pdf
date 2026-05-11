# codex-edge

Cloudflare Worker that puts the codex-pdf probe + extract endpoints on
the Cloudflare global edge. KV-backed write-through cache. Origin is
the Railway codex-pdf service.

## Deployed (production)

- **Worker URL**: <https://codex-edge.thinkneverland.workers.dev>
- **Account**: `99aa3f9229469650a746a7d39ac58448` (`Quincy@thinkneverland.com's Account`)
- **KV namespace `CACHE`**: `89a21ce1937046018a3d9d38f4e763ff` (preview `a4856d6f3b244087b907c189c2a2277d`)
- **Origin** (`CODEX_ORIGIN_URL`): `https://codex-pdf-lint-sidecar-production.up.railway.app`
- **Codex version pinned**: `1.8.0` (`CODEX_VERSION` var — bump on origin release)
- **TTLs**: probe 24 h, Phase 1 24 h, Phase 2 7 d

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

Manual: from this directory,

```sh
export CLOUDFLARE_API_TOKEN=<scoped-token>
export CLOUDFLARE_ACCOUNT_ID=99aa3f9229469650a746a7d39ac58448
wrangler deploy
```

Required token scopes: `Workers Scripts Write`, `Workers KV Storage
Write`, `Workers Routes Write`, `Account Settings Read`.

CI: `main` push to the parent codex-pdf repo deploys via the GH
Action in `codex-edge/.github/workflows/deploy.yml`. Required repo
secrets:

- `CLOUDFLARE_API_TOKEN` — scoped as above
- `CLOUDFLARE_ACCOUNT_ID` — `99aa3f9229469650a746a7d39ac58448`

## Operations

- The Worker is **fail-open**: any KV / Worker error proxies the
  request to origin transparently.
- `GET /edge/healthz` reports origin RTT and the deployed
  `CODEX_VERSION` so probes can confirm the version-skew between
  edge and origin.
- Anything not on the cached path forwards to origin untouched, so
  the Worker is a drop-in DNS replacement for the Railway URL.
