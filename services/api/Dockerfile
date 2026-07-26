# Multi-stage build for the AeroTwin API service.
#
#   base    -- shared Python runtime + workspace libraries
#   dev     -- adds dev tooling, expects source bind-mounts, runs with --reload
#   runtime -- slim non-root production image
#
# Built from the repository root so the shared `libs/` workspace is in context:
#   docker build -f services/api/Dockerfile -t aerotwin/api .

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency metadata first so the layer cache survives source edits.
COPY libs/at_core/pyproject.toml           libs/at_core/
COPY libs/at_config/pyproject.toml         libs/at_config/
COPY libs/at_observability/pyproject.toml  libs/at_observability/
COPY services/api/pyproject.toml           services/api/

COPY libs/ libs/
COPY services/api/ services/api/

RUN pip install --no-cache-dir \
        -e libs/at_core \
        -e libs/at_config \
        -e libs/at_observability \
        -e services/api

# ── development ───────────────────────────────────────────────────────────────
FROM base AS dev

RUN pip install --no-cache-dir \
        pytest pytest-asyncio pytest-cov hypothesis httpx \
        ruff mypy import-linter

ENV AT_PROFILE=docker \
    AT_LOG_JSON=false

EXPOSE 8000
CMD ["uvicorn", "at_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── production ────────────────────────────────────────────────────────────────
FROM base AS runtime

# Run unprivileged: a container that never needs root should never have it.
RUN useradd --create-home --uid 10001 aerotwin \
    && chown -R aerotwin:aerotwin /app
USER aerotwin

ENV AT_PROFILE=docker \
    AT_LOG_JSON=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

# Workers are set by the orchestrator; a single worker per container keeps
# the WebSocket connection registry coherent (Doc 13 section 13.7).
CMD ["uvicorn", "at_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
