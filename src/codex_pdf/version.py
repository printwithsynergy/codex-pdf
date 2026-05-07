"""Package version.

1.3.0 adds:
- SSRF hardening on the URL-ingest path (literal-IP connect, DNS
  rebinding defense, redirect cap with per-hop revalidation, private
  / link-local / loopback allow-list).
- ``POST /v1/walk/type4`` endpoint and ``codex_pdf.eval.ps_type4``
  module so PDF Type-4 PostScript byte-level evaluation lives in
  codex. lint-pdf consumes it via the client.

Schema is still v1.0.0 — every change is additive.
"""

VERSION = "1.3.0"
__version__ = VERSION
