"""Codex client SDK (Python).

Use :class:`HttpClient` to call the codex render + extract surface.
The client picks its mode from environment:

- ``CODEX_API_BASE`` set → HTTP mode (urllib + retries).
- ``CODEX_API_BASE`` unset and ``CODEX_LOCAL_FALLBACK=1`` → in-process
  fallback that calls :mod:`codex_pdf.render` and
  :mod:`codex_pdf.extract` directly.

Auth (HTTP mode):

- ``CODEX_BEARER_TOKEN`` → ``Authorization: Bearer ...``
- ``CODEX_API_KEY`` → ``X-Codex-Key``
- ``CODEX_INTERNAL_TOKEN`` → ``X-Codex-Internal``

Timeouts via ``CODEX_TIMEOUT_MS`` (default 60000).
"""

from codex_pdf.client.http_client import (
    CodexClientError,
    ColorSample,
    DensitometerSample,
    HeatmapResult,
    HttpClient,
    SeparationsResult,
)

__all__ = [
    "CodexClientError",
    "ColorSample",
    "DensitometerSample",
    "HeatmapResult",
    "HttpClient",
    "SeparationsResult",
]
