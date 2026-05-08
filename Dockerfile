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

# Build the venv as root so the global cache is shared, then chown
# everything to the non-root runtime user. Without this, `uv run` at
# container start tries to repair the editable install and fails on
# `/opt/venv/.../bin/codex-pdf: Permission denied` because the bin
# entries land outside the dir tree the runtime user can write.
# Install the `redis` extra unconditionally so a deploy that wires
# CODEX_REDIS_URL to a Railway Redis service uses the shared cache,
# AND a deploy that deletes the redis service still boots cleanly
# (codex falls back to in-memory; see codex_pdf.api.cache.make_cache).
RUN uv sync --frozen --no-dev --extra redis \
 && chown -R codex:codex /opt/venv /app

USER codex

ENV PORT=8080 \
    CODEX_AUTH_MODE=none \
    CODEX_LOCAL_FALLBACK=1 \
    UV_NO_SYNC=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
# Bypass `uv run` at runtime — the venv is already prepared. Calling
# uvicorn directly from /opt/venv/bin avoids any sync attempt against
# the read-only image and lets the entrypoint expand `$PORT`.
CMD ["sh", "-c", "/opt/venv/bin/uvicorn codex_pdf.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
