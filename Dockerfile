# Codex shared service image. Single source of truth for shared,
# per-consumer, and hybrid Railway deployments. The same image runs
# the FastAPI surface (`POST /v1/extract`, `/v1/render/...`, etc.).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_INSTALLER_METADATA=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# System packages:
# - ghostscript (>=10) for png16m + tiffsep + simulate-overprint
# - poppler-utils for pdftoppm/pdftotext fallbacks (TAC heatmap text bboxes)
# - libheif1 for heif/avif preview ingest paths consumers might rely on
# - tini reaps zombie children spawned by gs/pdftotext
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ghostscript \
        poppler-utils \
        libheif1 \
        tini \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Non-root user.
RUN useradd --system --create-home --shell /usr/sbin/nologin codex
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY schemas ./schemas
COPY tests ./tests

RUN uv sync --frozen --no-dev

USER codex

ENV PORT=8080 \
    CODEX_AUTH_MODE=none \
    CODEX_LOCAL_FALLBACK=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uv run uvicorn codex_pdf.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
