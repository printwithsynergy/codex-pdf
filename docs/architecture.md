---
title: "Architecture"
description: "codexPDF boundaries, extraction + render pipeline, and the three deployed services that share one cache key namespace."
group: "Getting started"
order: 2
---

# Architecture

`codexPDF` is a contract-first facts engine for PDF documents.

## Boundary

- Read-only extraction + render. Codex never produces new PDF bytes
  — `scripts/produce_surface_audit.py` enforces this on every CI
  run.
- No customer policy / rule adjudication. Codex emits detection
  signals; pass/fail belongs to **Lint**.
- No display / viewer presentation. PNG renders are byte-accurate
  source-of-truth for **Loupe** to display, not a viewer in
  themselves.
- Consumer-agnostic output: same JSON contract regardless of
  caller.

## Pipeline

1. Input PDF bytes are loaded by the extractor layer (PyMuPDF
   for the fast path, pikepdf for slower per-object inspection).
2. Domain extractors populate `CodexDocument` fields: pages,
   boxes, fonts, images, color spaces (with Separation tint
   transforms evaluated at `t=1.0` so spot inks land on the right
   swatch), OCG / layers, annotations, transparency, trapping,
   form XObjects.
3. Output is serialized as JSON against the published schemas in
   `schemas/v1/`. Each section (document, color, geom) versions
   independently and reports its `schema_version` inline.
4. Render endpoints rasterize pages, separations, TAC heatmaps,
   and OCG-isolated layers via Ghostscript + PyMuPDF.

## Primary contract

- Runtime model: `codex_pdf.models.v1.CodexDocument`
- Document schema: `schemas/v1/codex-document.schema.json`
- Section versions: `codex_pdf.color.COLOR_SCHEMA_VERSION`,
  `codex_pdf.geom.GEOM_SCHEMA_VERSION`
- Live manifest: `GET /v1/contract`

## Deployed surface

In production, codex runs as **three services** sharing one
content-addressed cache namespace
(`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`), so a `VERSION`
bump invalidates every tier atomically. The full deployed map —
URLs, account / service IDs, and the version-bump checklist —
lives in [`CLAUDE.md`](../CLAUDE.md).

1. **codex-pdf API** (Railway) — FastAPI under gunicorn + uvicorn
   workers. Bearer + internal token auth. Backed by Redis for
   cache and blob storage.
2. **codex-speculator** (Railway sidecar) — a Redis-Streams
   consumer. `POST /v1/probe` and the blob-store put both XADD a
   sha onto the `codex:speculate` stream; the speculator runs
   Phase 1 + Phase 2 ahead of the next request so `/v1/extract`
   lands warm. Idempotent — cache-hit short-circuit collapses
   replays to a single Redis GET.
3. **codex-edge** (Cloudflare Worker + KV) — drop-in DNS-level
   replacement that captures every probe / extract SSE frame and
   replays from KV on the next hash-keyed request. Multipart
   uploads bypass to origin. `ctx.waitUntil` keeps the Worker
   alive long enough to persist every frame before the response
   stream closes.

## Consumer relationship

Downstream engines (`lint-pdf`, `loupe-pdf`, marketing demos)
treat codex output as the source of truth for document facts and
keep any product-specific behaviour in adapter layers. New
products map to one owner per capability — see
[`docs/service-ownership-contract.md`](./service-ownership-contract.md).
