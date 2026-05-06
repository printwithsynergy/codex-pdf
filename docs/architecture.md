---
title: "Architecture"
description: "codexPDF boundaries, extraction pipeline shape, and the contract-first model used by downstream tools."
group: "Getting started"
order: 2
---

# Architecture

`codexPDF` is a contract-first facts engine for PDF documents.

## Boundary

- Read-only extraction only.
- No rendering, layout, mutation, or rule adjudication.
- Consumer-agnostic output: same contract regardless of caller.

## Pipeline

1. Input PDF bytes are loaded by the extractor layer.
2. Domain extractors populate `CodexDocument` fields (pages, boxes, fonts,
   images, color spaces, OCG/layers, annotations, transparency, trapping).
3. Output is serialized as JSON against published schema definitions in
   `schemas/v1/`.

## Primary contract

- Runtime model: `codex_pdf.models.v1.CodexDocument`
- Schema: `schemas/v1/codex-document.schema.json`
- Version marker: `schema_version` field in payload

## Consumer relationship

Downstream engines should treat codex output as the source of truth for
document facts and keep any product-specific behavior in adapter layers.
