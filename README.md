---
title: "Overview"
description: "Authoritative read-only PDF facts engine for Think Neverland tools. Versioned contract, schema-validated output, and consumer-agnostic extraction."
group: "Getting started"
order: 1
slug: "overview"
---

# codexPDF

`codexPDF` is Think Neverland's authoritative, read-only PDF facts reference.

Other engines consult `codexPDF` for canonical document facts instead of
re-parsing PDFs independently. The contract is versioned and schema-validated.

## Status

Current baseline includes:

- Python package (`codex_pdf`) with typed models
- CLI (`codex-pdf extract|schema|validate|probe|parity`)
- Versioned schemas in `schemas/v1/`
- Golden output harness under `tests/golden/`

## Quickstart

```bash
uv sync
uv run codex-pdf probe input.pdf --json
uv run codex-pdf extract input.pdf --pretty > out.json
uv run codex-pdf validate out.json
uv run codex-pdf parity --fixtures-root tests/fixtures --profile summary --max-files 5
uv run codex-pdf parity --fixtures-root tests/fixtures --profile inventory --max-files 5
uv run codex-pdf parity --fixtures-root tests/fixtures --profile deep --max-files 5
```

Optional external baseline comparison (consumer-specific adapter provided at runtime):

```bash
uv run codex-pdf parity \
  --fixtures-root /path/to/pdfs \
  --profile summary \
  --baseline-command "<your_command_with_{pdf}_placeholder>"
```

## Contract

The public API is the JSON contract rooted at `CodexDocument`.

- Schema path: `schemas/v1/codex-document.schema.json`
- Runtime model: `codex_pdf.models.v1.CodexDocument`
- Stability policy: SemVer (`major` for breaking contract changes)

## Documentation

| Topic | Doc |
| --- | --- |
| Architecture and boundaries | [docs/architecture.md](./docs/architecture.md) |
| CLI commands and usage patterns | [docs/cli.md](./docs/cli.md) |
| Contract and schema versioning | [docs/contract.md](./docs/contract.md) |
| Parity profiles and baselines | [docs/parity.md](./docs/parity.md) |
| Preflight ingest adapters | [docs/preflight-ingest.md](./docs/preflight-ingest.md) |
| Migration sequencing | [docs/migration-plan.md](./docs/migration-plan.md) |
| Legacy discovery audit | [docs/discovery-audit.md](./docs/discovery-audit.md) |
| Backward compatibility requirements | [docs/backward-compatibility.md](./docs/backward-compatibility.md) |
| Cleanup stop-gates policy | [docs/cleanup-stop-gates.md](./docs/cleanup-stop-gates.md) |

## License

AGPL-3.0-or-later.
