---
title: "CLI"
description: "Command reference for extract, probe, render, serve, parity, schema, contract, and validate workflows."
group: "Getting started"
order: 3
---

# CLI

`codex-pdf` exposes a contract-oriented CLI built with `argparse`.
The same code path that the HTTP API uses runs in-process when you
invoke the CLI, so output is byte-for-byte identical to the
deployed surface.

## Commands

| Command | Purpose |
|---|---|
| `extract <pdf>` | Emit the full `CodexDocument` JSON. |
| `probe <pdf>` | Two-event metadata probe (page count, dimensions, info dict, `pdf_sha256`). |
| `schema [name]` | Print a published JSON schema (default: `codex-document`). |
| `contract` | Print the machine-readable contract manifest (endpoint inventory + section schema versions). |
| `validate <codex_json>` | Validate a codex JSON payload against the published schema. |
| `parity` | Compare codex projections against a baseline command. |
| `render page` | Render one page to PNG. |
| `render separations` | Render every separation channel for one page. |
| `render heatmap` | Render a TAC heatmap PNG plus a per-run header. |
| `render layer` | Render one OCG-isolated layer to RGBA PNG. |
| `serve` | Start the codex HTTP API (uvicorn, in-process). |

## Common usage

```bash
uv run codex-pdf extract input.pdf --pretty > out.json
uv run codex-pdf validate out.json
uv run codex-pdf probe input.pdf --json
uv run codex-pdf contract --pretty
```

## Streaming probe / extract (HTTP only)

The CLI's `probe` and `extract` are synchronous. The deployed HTTP
API also exposes streaming variants that emit Phase 1 results as
soon as PyMuPDF is finished and Phase 2 once pikepdf adds the
slower fields:

- `POST /v1/probe` — server-sent events with two frames (`probe-min`
  immediately, `probe-std` after the secondary parse).
- `POST /v1/extract/stream` — same shape for full extraction; pass
  `?granular=1` to get per-section progress events.

The TypeScript client's `probeStream()` and `extractStream()` wrap
this directly; the Python `codex_pdf.client.HttpClient` also has
streaming helpers when used against a remote API.

## Render usage

```bash
uv run codex-pdf render page input.pdf --page 0 --dpi 144 -o page.png
uv run codex-pdf render separations input.pdf --page 0 -o seps/
uv run codex-pdf render heatmap input.pdf --page 0 -o tac.png
uv run codex-pdf render layer input.pdf --page 0 --ocg "Dieline" -o dieline.png
```

## Parity usage

```bash
uv run codex-pdf parity \
  --fixtures-root tests/fixtures \
  --profile deep \
  --max-files 10
```

Baseline command mode:

```bash
uv run codex-pdf parity \
  --fixtures-root /path/to/pdfs \
  --profile summary \
  --baseline-command "<command with {pdf} placeholder>"
```

## Local server

```bash
uv run codex-pdf serve --host 0.0.0.0 --port 8080
curl localhost:8080/v1/version
```

The same image, in production, runs under gunicorn + uvicorn workers
via the Dockerfile's `CMD` — see [`docs/deploy.md`](./deploy.md).
