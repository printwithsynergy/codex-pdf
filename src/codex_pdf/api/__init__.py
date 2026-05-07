"""Codex HTTP API package.

Exposes the :data:`app` FastAPI ASGI application. Run with:

.. code-block:: bash

    uv run uvicorn codex_pdf.api.main:app --host 0.0.0.0 --port 8080
"""

from codex_pdf.api.main import app

__all__ = ["app"]
