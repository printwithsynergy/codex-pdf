# Contributing to codex-pdf

`codex-pdf` is the read-only PDF facts + render service for the
Print with Synergy / Think Neverland tool family. It owns
extraction, normalization, and reusable summary payloads — keeping
outputs deterministic, versioned, and backward-compatible.

Before opening a PR, please confirm your change fits codex's lane
(extraction / normalized facts / detection signals) rather than a
display or rules concern. UI presentation belongs to **Loupe** and
rule pass/fail logic belongs to **Lint** — see
[`docs/service-ownership-contract.md`](./docs/service-ownership-contract.md).

## Local setup

```bash
git clone https://github.com/printwithsynergy/codex-pdf
cd codex-pdf
uv sync
```

## Running the test suite

The full sweep, including the producer-surface audit that gates the
read-only invariant:

```bash
uv run pytest -q
uv run python scripts/produce_surface_audit.py
```

TypeScript client + Cloudflare Worker:

```bash
cd clients/ts && npm test
cd ../../codex-edge && npx tsc --noEmit && npx vitest run
```

## Conventions

- **Schema additions are additive.** Adding a field is a minor bump;
  changing a field shape or removing a field is a major bump. See
  [`docs/backward-compatibility.md`](./docs/backward-compatibility.md).
- **Read-only.** Codex never produces new PDF bytes. The audit at
  `scripts/produce_surface_audit.py` enforces this; it must stay
  green on every commit.
- **No customer policy.** Detection signals belong here; pass/fail
  verdicts belong in Lint.
- **Branches** are named `claude/<short-topic>-<version>` for agent
  work; humans use whatever they like.
- **Commits** follow Conventional Commits (`feat:`, `fix:`, `chore:`,
  `perf:`, `docs:`).

## Releasing a new VERSION

When bumping `codex_pdf.version.VERSION`:

1. Update `pyproject.toml`, `src/codex_pdf/version.py`,
   `clients/ts/package.json`, `codex-edge/wrangler.toml`
   (`CODEX_VERSION`), and the deployed-surface heading in
   [`CLAUDE.md`](./CLAUDE.md).
2. `uv build` → publish wheel + sdist to PyPI.
3. `npm publish --access public` from `clients/ts`.
4. `wrangler deploy` from `codex-edge` so KV cache keys rotate.
5. Railway autodeploys the API + speculator from `main`.

Cache keys (`codex:{VERSION}:{kind}:{pdf_sha}:{args_sha}`) rotate
atomically across all three tiers when `VERSION` changes — no KV
purge needed.

## Reporting bugs

Please open a GitHub issue with:

- A minimal reproducing PDF (or its `pdf_sha256` and how it was
  generated) when the bug is content-dependent.
- The `codex-pdf --version` (or the `version` field returned by
  `GET /v1/version`).
- The exact request / CLI invocation.

For security issues, see [`SECURITY.md`](./SECURITY.md) — please do
**not** open a public issue.
