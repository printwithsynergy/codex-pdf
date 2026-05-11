---
title: "Overview"
description: "Authoritative read-only PDF facts + render engine for Print with Synergy / Think Neverland tools. Versioned contract, schema-validated output, deployed as three services."
group: "Getting started"
order: 1
slug: "overview"
---

# codexPDF

`codexPDF` is the authoritative, read-only PDF facts + render reference
for the Print with Synergy / Think Neverland tool family.

Other engines consult `codexPDF` for canonical document facts instead
of re-parsing PDFs independently. The contract is versioned and
schema-validated.

## Status

`codex-pdf 1.8.1`. Current surface includes:

- Python package (`codex_pdf`) with typed `pydantic` models.
- CLI (`codex-pdf extract|schema|contract|validate|probe|parity|render|serve`).
- HTTP API (`/v1/extract`, `/v1/probe`, `/v1/extract/stream`,
  `/v1/render/{page,separations,heatmap,layer}`,
  `/v1/sample/{color,density}`, `/v1/walk/{type4,content-stream}`,
  `/v1/color/{resolve,match-pantone,neutral-density,inkbook}`,
  `/v1/geom/{tile,intersect,union,difference,offset}`,
  `/v1/retention/delete`).
- TypeScript client (`@printwithsynergy/codex-client`) mirroring the
  Python `codex_pdf.client` surface, with SSE streaming for probe
  and extract.
- Versioned schemas in `schemas/v1/` (document, color, geom).
- Cloudflare Worker (`codex-edge`) providing a KV-backed
  write-through cache layer in front of the API.
- Redis-Streams speculator (`codex-speculator`) that pre-warms
  Phase 1 + Phase 2 caches.
- Opt-in retention to Cloudflare R2 for the marketing demo:
  `retain_for_training=true` on `POST /v1/extract` persists the
  PDF + extract + metadata under a hive-partitioned key; the
  default remains "delete bytes on response". See
  [`CLAUDE.md`](./CLAUDE.md) for the deployed bucket layout.

See [`CLAUDE.md`](./CLAUDE.md) for the full deployed-service map
(URLs, account IDs, version-bump checklist).

## Quickstart

```bash
uv sync
uv run codex-pdf probe input.pdf --json
uv run codex-pdf extract input.pdf --pretty > out.json
uv run codex-pdf validate out.json
uv run codex-pdf parity --fixtures-root tests/fixtures --profile summary --max-files 5
```

Run the HTTP API locally:

```bash
uv run codex-pdf serve --host 0.0.0.0 --port 8080
curl localhost:8080/v1/version
```

## Contract

The public surface is the JSON contract rooted at `CodexDocument`,
plus the per-section contracts under color and geom.

- Document schema: `schemas/v1/codex-document.schema.json`
- Runtime model: `codex_pdf.models.v1.CodexDocument`
- Stability policy: SemVer (`major` for breaking contract changes;
  field additions are minor bumps).
- Live contract endpoint: `GET /v1/contract` returns the endpoint
  inventory plus `section_schema_versions`.

## Documentation

| Topic | Doc |
| --- | --- |
| Architecture and boundaries | [docs/architecture.md](./docs/architecture.md) |
| CLI commands and usage | [docs/cli.md](./docs/cli.md) |
| Contract and schema versioning | [docs/contract.md](./docs/contract.md) |
| Deploying the API + speculator + edge | [docs/deploy.md](./docs/deploy.md) |
| Parity profiles and baselines | [docs/parity.md](./docs/parity.md) |
| Preflight ingest adapters | [docs/preflight-ingest.md](./docs/preflight-ingest.md) |
| Codex change ripple rule | [docs/operations/codex-change-ripple.md](./docs/operations/codex-change-ripple.md) |
| Marketing deploy template | [docs/operations/marketing-deploy-template.md](./docs/operations/marketing-deploy-template.md) |

## Contributing

We welcome PRs that fit codex's lane (extraction, normalization,
detection signals). Display concerns belong in **Loupe**; rule
pass/fail logic belongs in **Lint**.

Read [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the dev setup, test
commands, schema-bump rules, and release checklist.

## Security

Please report vulnerabilities privately to
**`security@thinkneverland.com`** — do not open a public issue.

The full disclosure policy, supported-version matrix, and scope
(including the read-only PDF invariant) live in
[`SECURITY.md`](./SECURITY.md).

## License

`codexPDF` is distributed under the **GNU Affero General Public
License v3.0 or later** (`SPDX-License-Identifier:
AGPL-3.0-or-later`). The full license text is in
[`LICENSE`](./LICENSE).

AGPL applies in particular when codex is reachable over a network —
modifications served to remote users must be made available to
those users under the same terms.
