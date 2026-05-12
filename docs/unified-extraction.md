# Unified Extraction — integration guide

This guide is for consumer services (preflight engines, viewers,
batch import pipelines) wiring against the codex-pdf unified
extraction API. It covers the surface, the cache-key contract,
tenancy, rate-limiting, error shapes, and the per-stage telemetry
that ships with every response.

Consumer-agnostic: nothing in this surface presumes a specific
caller. The same endpoints serve lint-pdf, loupe-pdf, compile-pdf,
and any future consumer.

## Endpoints in scope

| Verb / Path | Purpose | Cache key |
| --- | --- | --- |
| `POST /v1/extract` | First-stop. Returns the full CodexDocument. No field selection. | `(tenant, pdf_hash)` |
| `GET /v1/documents/{pdf_hash}/text-regions?page_index=N&dpi=N` | Second-stop. One page's detected regions, in PDF user-space points. | `(tenant, pdf_hash, page_index, dpi)` |
| `POST /v1/documents/{document_id}/conformance/{profile}` | Compute (or fetch from cache) a conformance verdict. | `(tenant, pdf_hash, profile)` |
| `GET /v1/documents/{pdf_hash}/renders` | List `(page_index, dpi, color_space)` tuples already in the render cache. | n/a (it's the index) |

The first-stop / second-stop split is intentional. `/v1/extract`
returns everything codex knows; consumers cherry-pick. Per-resource
endpoints let consumers that already have the codex doc ask for
exactly the slice they need without an extract-then-discard round
trip.

### Cache-key contract

Cache keys are part of the public contract — stable across releases:

- text-regions: `(pdf_hash, page_index, dpi)`
- conformance:  `(pdf_hash, profile)`
- render:       `(pdf_hash, page_index, dpi, color_space)`

The codex implementation also scopes by tenant (see below) but
the tenant component is transparent to most consumers and isn't
part of the contract the caller cares about — it's a server-side
isolation knob.

## Tenancy

Every request can carry an `X-Codex-Tenant` header. The server:

1. Normalises the value (`[a-z0-9][a-z0-9-]{0,62}`; falls back to
   `"default"` for missing/invalid).
2. Scopes the cache lookup, the blob store, and the renders
   index by tenant.

A hash uploaded by Tenant A is invisible to Tenant B even if B
learns the hash. The 412 message on a blob miss is **intentionally
identical** for "wrong tenant" and "expired" — probing isn't
informative.

```python
# Python client
from codex_pdf.client import HttpClient

client = HttpClient(
    base_url="https://codex.example.com",
    bearer_token="…",
    tenant="acme-corp",   # surfaces as X-Codex-Tenant on every request
)
```

```ts
// TypeScript client
import { HttpClient } from "@printwithsynergy/codex-client";

const client = new HttpClient({
    baseUrl: "https://codex.example.com",
    bearerToken: "…",
    tenant: "acme-corp",   // surfaces as X-Codex-Tenant on every request
});
```

Both clients also read the tenant from the `CODEX_TENANT` env when
the option is omitted.

## Rate limiting

Compute-and-cache POSTs (`/v1/extract`, render, sample, walk,
conformance) consult an in-process token bucket per
`(tenant, endpoint)`. Bucket exhausted → `429 Too Many Requests`
with a `Retry-After` header in seconds.

Both bundled clients honour `Retry-After` and back off
automatically; consumers using raw HTTP should do the same.

Operator knobs (env, codex-pdf service):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_RATE_LIMIT_RPM` | `120` | Refills per minute |
| `CODEX_RATE_LIMIT_BURST` | `30` | Bucket size |
| `CODEX_RATE_LIMIT_DISABLED` | `false` | Off-switch |

The limiter is in-process and per-replica. Multi-replica fleets
see effective limit `N × rpm`.

## Error shapes

Every 4xx/5xx response uses the shared envelope:

```json
{ "detail": "human-readable message" }
```

The new endpoints document their per-status shapes in OpenAPI
under `responses=`:

- `400 Bad Request` — invalid `pdf_hash`, `page_index`, `dpi`, or
  unknown conformance profile.
- `404 Not Found` — no PDF cached for `(tenant, document_id)`.
  Upload via `/v1/extract` first.
- `429 Too Many Requests` — rate limit exceeded. `Retry-After`
  header carries the wait in seconds.

## Stage telemetry

Every response carries per-stage wall-clock timing in two places:

1. **Response envelope**: `stage_durations_ms: { stage: int_ms }`.
2. **Response header**: `X-Codex-Stage-Durations-Ms` (same dict
   serialised as JSON).

The header is there for transports that strip envelope bodies
(in-process clients, mocks). Both clients back-fill the envelope
from the header when present.

Initial stage names:

- `extract` — full CodexDocument parse.
- `render` — page render.
- `text_regions` — detected text regions per page.
- `conformance` — verdict compute for one profile.

New stage names are non-breaking: consumers must treat unknown
keys as opaque.

## Observability

Prometheus metrics on the codex-pdf service (`/metrics`):

| Metric | Type | Labels |
| --- | --- | --- |
| `codex_api_requests_total` | Counter | `endpoint`, `status` |
| `codex_api_request_seconds` | Histogram | `endpoint` |
| `codex_api_cache_lookups_total` | Counter | `endpoint`, `outcome` (hit/miss) |
| `codex_api_stage_seconds` | Histogram | `stage` |

The stage histogram observes the same numbers consumers see in
`stage_durations_ms`. Cache hit rate per endpoint = `rate(codex_api_cache_lookups_total{outcome="hit"}[5m]) / rate(codex_api_cache_lookups_total[5m])`.

## Conformance — supported profiles

| Profile | Notes |
| --- | --- |
| `pdfx4` | OutputIntent + Trapped + PDF ≥1.4 + XMP pdfxid |
| `pdfx1a` | OutputIntent + Trapped + PDF=1.3 |
| `pdfx3` | OutputIntent + Trapped + PDF ≥1.3 |
| `pdfa1b` / `pdfa2b` / `pdfa3b` | XMP present + not encrypted + correct pdfaid:part |
| `pdfua1` | XMP present + pdfuaid + non-empty Title |

The profile enum is **forward-compatible**. Consumers must treat
unknown profile strings (e.g. a future `pdfx6`, `pdfa4`) as
opaque so an older client doesn't break against a newer server.

Clause coverage is the minimum-viable set in the rc.x series. Full
ISO coverage lands in later phases; the framework is registry-
driven, so new clauses are additive only.

## AI signals (1.11.0)

Codex 1.11.0 implements the AI Signal contract frozen in 1.10.0.
The extracted `CodexDocument` carries six AI signal surfaces:

| Field | Scope | Backend | Purpose |
| --- | --- | --- | --- |
| `detected_language` | per page | Claude Haiku (text) | BCP-47 tag + confidence. |
| `detected_logos` | per page | Claude Sonnet (vision) | Brand identity + bbox in PDF user-space points. |
| `detected_symbols` | per page | Claude Sonnet (vision) | Regulatory / safety / sustainability symbols (GHS, recycling, FDA, CE, ™, ©, etc.). |
| `detected_barcodes` | per page | pyzbar + pylibdmtx (CPU) | Decoded value + format + bbox. No Claude cost. |
| `spell_candidates` | per page | Claude Haiku (text) | Suspect words for lint-pdf's tenant spell rule. |
| `document_classification` | document | Claude Haiku (text) | Probability map (`{"label": 0.7, "folding_carton": 0.2}`). |

The dedicated endpoint `GET /v1/documents/{pdf_hash}/signals/{kind}`
returns the same shapes scoped to one signal kind, so consumers can
re-fetch a single signal without re-running the full extract. Pass
`?page_index=N` for page-scoped kinds (`language`, `logos`,
`symbols`, `barcodes`, `spell`); `classification` is document-scoped
so the parameter is ignored.

Codex emits a structured `CodexWarning` on every `/v1/extract`
response describing the AI lane's state:

| Warning `code` | When |
| --- | --- |
| `ai_disabled` | Operator gate (`CODEX_AI_ENABLED`) is off. |
| `ai_skipped` | Caller sent `X-Codex-Skip-AI: true`. |
| `ai_missing_credentials` | Operator opted in but `anthropic` SDK isn't importable or `ANTHROPIC_API_KEY` is unset. |
| `ai_tier` | Advisory — AI ran. `message` carries `cpu+claude` or `gpu` plus the realised dollar spend. |
| `ai_budget_exceeded` | Per-request cost cap (`CODEX_AI_COST_CAP_USD_PER_REQUEST`, default `$0.10`) was hit mid-extract. |

See [`policies.md`](./policies.md#ai-signals-130) for the full
warning catalogue, cache-key contract, and the two-backend
(CPU + Claude default vs optional GPU) policy.

## End-to-end example

```python
from codex_pdf.client import HttpClient

client = HttpClient(
    base_url="https://codex.example.com",
    bearer_token="…",
    tenant="acme-corp",
)

# First stop — full payload, includes detected text regions per page.
doc = client.extract(pdf_bytes)
sha = doc["pdf_sha256"]

# Second-stop re-fetch — one page only, cache-hit on second call.
regions_page_0 = client.text_regions(sha, page_index=0, dpi=150)
print(len(regions_page_0["regions"]))

# Compute a verdict; cached on the server.
verdict = client.conformance(sha, "pdfx4")
print(verdict["passed"], verdict["clauses"])

# What renders already exist in the cache?
print(client.list_renders(sha)["renders"])
```

```ts
import { HttpClient } from "@printwithsynergy/codex-client";

const client = new HttpClient({
    baseUrl: "https://codex.example.com",
    bearerToken: "…",
    tenant: "acme-corp",
});

const doc = await client.extract(pdfBytes);
const sha = doc.pdf_sha256;

const regions = await client.getTextRegions(sha, { pageIndex: 0, dpi: 150 });
const verdict = await client.computeConformance(sha, "pdfx4");
const renders = await client.listRenders(sha);
```

## Versioning

Schema version (the codex-document contract) and package version
move on different cadences:

- Schema version (`schema_version` in the payload) — only bumped
  when the CodexDocument contract changes.
- Package version (`pyproject.toml` / `package.json`) — bumped on
  every release. Pre-release tags (`rcN`) signal in-flight phases.

The cache-key version segment (`{VERSION}` in
`codex:{VERSION}:{kind}:{tenant}:{pdf_sha}:{args_sha}`) tracks the
package version so a deploy that bumps either dimension invalidates
the cache atomically.
