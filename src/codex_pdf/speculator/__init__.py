"""Codex speculator sidecar.

Reads sha values off the ``codex:speculate`` Redis Stream — published
by the codex-pdf API on probe and blob-put events — and pre-runs
``extract_document_fast`` + ``extract_document`` so the responses
are already cached when the originating client follows up.

Origin behaviour does not depend on the speculator. If the sidecar
is offline, ``/v1/probe`` and ``/v1/extract`` still serve at the same
latency as today; speculation is purely an opportunistic pre-warm.

Run as a separate Railway service with the same Docker image as the
API: see ``railway.speculator.toml``. The ``__main__`` entrypoint
loops on ``XREAD`` and exits cleanly on SIGTERM.
"""

from codex_pdf.speculator.consumer import SpeculatorConsumer, run_forever

__all__ = ["SpeculatorConsumer", "run_forever"]
