# Codex shared service image. Single source of truth for shared,
# per-consumer, and hybrid Railway deployments. The same image runs
# the FastAPI surface (`POST /v1/extract`, `/v1/render/...`, etc.).
#
# Two-stage build:
#  - `builder` carries gcc / g++ / cmake / ninja so `uv sync` can
#    compile the optional `[geom]` extra (pyclipr -> Clipper2 C++)
#    from sdist when no manylinux wheel is available for our arch.
#  - `base` is the runtime image; only the populated /opt/venv is
#    copied in, keeping the final image lean (~280 MB) while still
#    shipping the geom + redis surfaces.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_INSTALLER_METADATA=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        python3-dev \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY schemas ./schemas
COPY tests ./tests

RUN uv sync --frozen --no-dev --extra redis --extra geom --extra retain


FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Runtime system packages:
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

# Non-root user.
RUN useradd --system --create-home --shell /usr/sbin/nologin codex
WORKDIR /app

# Copy the pre-built venv from the builder stage and the project
# sources. Chown to the runtime user so editable installs / module
# imports don't trip on permission errors at boot.
COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY schemas ./schemas
COPY tests ./tests

RUN chown -R codex:codex /opt/venv /app

USER codex

ENV PORT=8080 \
    CODEX_AUTH_MODE=none \
    CODEX_LOCAL_FALLBACK=1 \
    UV_NO_SYNC=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
# CODEX_MODE picks the entrypoint:
#   - unset / "api" (default): gunicorn + uvicorn workers serving the FastAPI
#     app. CODEX_WORKERS defaults to 2 (safe for Railway hobby — 2 shared
#     vCPUs); set higher on Pro/Team plans. Single-replica deployments still
#     benefit from 2 workers so one extraction doesn't block another.
#   - "speculator": run the Redis-Stream consumer that pre-warms Phase 1 +
#     Phase 2 caches ahead of the next request. No HTTP listener.
CMD ["sh", "-c", "if [ \"$CODEX_MODE\" = speculator ]; then exec /opt/venv/bin/python -m codex_pdf.speculator; else exec /opt/venv/bin/gunicorn -w ${CODEX_WORKERS:-2} -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8080} --timeout 120 --graceful-timeout 30 codex_pdf.api.main:app; fi"]
