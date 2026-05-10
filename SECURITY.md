# Security policy

`codex-pdf` parses untrusted PDF input on behalf of upstream services
(Loupe, Lint, marketing demos). We take vulnerability reports
seriously — especially around PDF parsing, SSRF, authentication, and
cache-poisoning vectors.

## Reporting a vulnerability

Please **do not open a public GitHub issue**.

Email security disclosures to **`security@thinkneverland.com`**.
If you need an encrypted channel, request our PGP key in the first
message and we will reply with the fingerprint and public key.

A useful report includes:

- The codex-pdf version (`GET /v1/version` or the published package
  version on PyPI / npm).
- Whether the issue is reachable through the deployed Railway API,
  the codex-edge Cloudflare Worker, the local CLI, the Python or
  TypeScript SDK — or some combination.
- A minimal reproducer: a PDF / JSON request body / curl invocation
  that triggers the issue.
- The behaviour observed and the behaviour you expected.

## Response timeline

- We acknowledge receipt within **two business days**.
- We aim to ship a fix or mitigation within **30 days** for
  high-severity issues, **90 days** for low-severity.
- You will be credited in the release notes (and the security
  advisory, when one is published) unless you ask to remain
  anonymous.

## Supported versions

Only the latest minor release is patched for security issues. Older
minors get advisories but no backports.

| Version | Status |
|---------|--------|
| `1.7.x` | ✅ patched |
| `< 1.7` | ❌ unsupported — please upgrade |

## In scope

- The Python package (`pip install codex-pdf`) and its CLI.
- The TypeScript client (`@printwithsynergy/codex-client`).
- The deployed Railway HTTP API
  (`codex-pdf-lint-sidecar-production.up.railway.app`).
- The Cloudflare Worker (`codex-edge.thinkneverland.workers.dev`).
- The codex-speculator Redis-Streams consumer.

## Out of scope

- Issues in upstream dependencies (PyMuPDF, pikepdf, Ghostscript)
  that have already been disclosed upstream — please report those
  to the upstream project.
- Denial-of-service from PDFs that legitimately take a long time to
  parse but are not malformed (for example, very large multi-page
  artwork). Codex sets a hard timeout; reports about *bypassing*
  that timeout are in scope.
- Self-inflicted misconfiguration (e.g. running with no auth tokens
  set on a public network).

## Scope of the read-only invariant

Codex never produces new PDF bytes. If you can demonstrate codex
emitting a PDF or a `b"%PDF-"` payload through any of the surfaces
above, that is a security finding even if no other compromise
follows — please report it.
