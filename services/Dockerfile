# syntax=docker/dockerfile:1.7
#
# Backend container for the Inspira planning-studio service.
#
# Build from the repo root with the services/ subtree as the build context:
#   docker build -f services/Dockerfile -t inspira-backend services/
#
# The image runs `python -m planning_studio_service`, binding to
# $PLANNING_STUDIO_HOST:$PLANNING_STUDIO_PORT. The container default host is
# 0.0.0.0 so the service is reachable from outside the container; override to
# 127.0.0.1 only if you're fronting it with a sidecar on the same network
# namespace.

FROM python:3.12-slim AS base

# Git SHA at build time. Pass with `--build-arg INSPIRA_GIT_SHA=$(git rev-parse --short HEAD)`.
# Surfaces on /api/status as the `version` field. Defaults to "dev" so a
# forgotten build-arg doesn't break healthchecks or the status page.
ARG INSPIRA_GIT_SHA=dev
ENV INSPIRA_GIT_SHA=${INSPIRA_GIT_SHA}

# Keep the image small and predictable.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is needed by the HEALTHCHECK below. Install only what we need, then
# clean up apt lists to keep layers lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy pyproject first so `pip install` can leverage layer caching: if only
# source files change, we still reuse the installed-deps layer.
COPY pyproject.toml ./
COPY planning_studio_service ./planning_studio_service
# Second first-party package: the MCP + OpenAPI Actions surface for
# Claude.ai and ChatGPT Custom GPTs. Shares the same store and auth as
# the main service; installed in the same `pip install -e .` below.
COPY inspira_mcp ./inspira_mcp
# Admin / break-glass tooling (password reset link, admin user create,
# kickoff smoke test, migration drift check, backup/restore wrappers).
# Shipped so they can be run on the Fly machine via `fly ssh console`
# instead of needing a local DB connection. Imports `planning_studio_service`
# directly — works because the package is installed editable in /app.
COPY scripts ./scripts
# Alembic schema migrations. Without these, new tables (voice_usage,
# user_access_tokens, ...) never get created in prod and the store's
# belt-and-braces column retrofits fail with "relation does not exist".
# scripts/migrate.sh expects alembic.ini one level up from scripts/, which
# means both of these must land at /app for the resolver to work.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Install the package in editable mode. This matches the local dev flow and
# avoids needing a separate wheel build step. Editable install is fine inside
# a container because the source tree is baked in and never edited at runtime.
RUN pip install --upgrade pip \
    && pip install -e .

# Create a non-root user for safety and a /data dir the user owns for the
# SQLite file and any durable transcripts. PLANNING_STUDIO_STORAGE_ROOT is
# pointed at /data by default so a single volume mount captures all state.
RUN useradd --create-home --shell /bin/bash inspira \
    && mkdir -p /data \
    && chown -R inspira:inspira /data /app

USER inspira

# Default runtime env. Everything is overridable at `docker run` / compose
# time. HOST is 0.0.0.0 (not 127.0.0.1) so the port is reachable from outside
# the container. DATABASE_URL is not yet read by the app but is forwarded so
# the Postgres migration can pick it up without another image build.
ENV PLANNING_STUDIO_HOST=0.0.0.0 \
    PLANNING_STUDIO_PORT=4174 \
    PLANNING_STUDIO_STORAGE_ROOT=/data \
    DATABASE_URL=""

# Document the ports. The actual bind port comes from PLANNING_STUDIO_PORT
# (main app) and INSPIRA_MCP_PORT (MCP process). Fly's [processes] table
# decides which entrypoint runs in each machine; both share this image.
EXPOSE 4174 4175

# SQLite + any on-disk artifacts live here. Declaring a VOLUME makes Docker
# treat the dir as externally managed so bind-mounts and named volumes both
# work cleanly.
VOLUME ["/data"]

# Hit the liveness endpoint. Uses curl's --fail so a non-2xx response counts
# as a failed healthcheck. Start-period gives the interpreter time to import.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent --show-error \
        "http://127.0.0.1:${PLANNING_STUDIO_PORT}/api/health" || exit 1

CMD ["python", "-m", "planning_studio_service"]
