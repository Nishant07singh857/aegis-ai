# syntax=docker/dockerfile:1
# =============================================================================
# AEGIS AI — backend production image (multi-stage)
#
# Build context is the REPO ROOT; build with:
#   docker build -f backend/Dockerfile -t aegis-backend:prod .
# or via infrastructure/docker/docker-compose.prod.yml
#
# - Python 3.11 (matches pyproject: python >=3.11,<3.12)
# - Dependencies resolved from backend/poetry.lock via Poetry (pinned)
# - Runtime runs as non-root user `aegis` (uid 10001)
# - Everything is env-driven: no secrets are baked into this image
# =============================================================================

ARG PYTHON_IMAGE=python:3.11-slim

# ---------------------------------------------------------------- builder -----
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.1.2 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_VIRTUALENVS_CREATE=true

WORKDIR /app

# Copy only dependency manifests first so the venv layer is cached across
# source-code changes.
COPY backend/pyproject.toml backend/poetry.lock ./

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && poetry install --only main --no-root

# ---------------------------------------------------------------- runtime -----
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# curl is required for the container HEALTHCHECK; the apt cache is removed in
# the same layer so no package lists linger in the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 aegis \
    && useradd --system --uid 10001 --gid aegis --no-create-home \
               --shell /usr/sbin/nologin aegis

WORKDIR /app

COPY --from=builder --chown=aegis:aegis /app/.venv /app/.venv

# Application code + migrations. (poetry project packages = ["app"]; alembic
# ships so migrations can run inside the container.)
COPY --chown=aegis:aegis backend/app ./app
COPY --chown=aegis:aegis backend/alembic ./alembic
COPY --chown=aegis:aegis backend/alembic.ini ./alembic.ini

USER aegis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/system/status || exit 1

# The FastAPI instance is `app` in backend/app/main.py
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
