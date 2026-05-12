"""``python -m codex_pdf.vision`` entrypoint.

Runs the vision sidecar FastAPI app under uvicorn. The bind address
matches Railway's expected interface (``0.0.0.0:$PORT``).
"""

from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("CODEX_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "codex_pdf.vision.app:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("CODEX_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
