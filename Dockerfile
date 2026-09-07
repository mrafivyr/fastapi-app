# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the FastAPI application.
#
#   docker build --target test .                    # run the suite; build fails if it fails
#   docker build --target prod -t fastapi-app .     # slim runtime image
#
# Layout:
#
#   base ──> deps ────────────────────> prod     (runtime deps only, no uv, non-root)
#             └──> deps-dev ──> test             (+ pytest, + tests/, suite runs at build)
#
# `pyproject.toml`/`uv.lock` are copied before the source, so editing `app/`
# does not invalidate the dependency layer.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.15


# ======================================================
# BASE — shared by the builder and the runtime
# ======================================================

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    # `app` is a virtual project (uv.lock: source = { virtual = "." }), so it is
    # never pip-installed — it is imported from the working directory.
    PYTHONPATH=/app \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app


# ======================================================
# DEPS — resolve the locked runtime dependencies
# ======================================================

FROM base AS deps

COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Only the lock inputs — no source — so this layer is cached across code changes.
COPY pyproject.toml uv.lock .python-version ./

# `--locked` fails the build if uv.lock is stale rather than silently re-resolving.
# `--no-install-project` skips the (virtual) project itself; `--no-dev` omits pytest.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev


# ======================================================
# DEPS-DEV — the same environment plus the `dev` group
# ======================================================

FROM deps AS deps-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project


# ======================================================
# TEST — runs the suite during the build
# ======================================================

FROM deps-dev AS test

COPY app ./app
COPY tests ./tests

# `tests/conftest.py` sets DATABASE_URL/REDIS_URL itself before importing
# anything under `app.`, and swaps in SQLite + FakeRedis + httpx.MockTransport,
# so no service links and no runtime configuration are needed here.
#
# pytest options (testpaths, asyncio_mode) come from pyproject.toml, already
# present from the deps stage.
RUN pytest

# Also usable interactively:  docker run --rm <image> pytest -k cache -v
CMD ["pytest"]


# ======================================================
# PROD — runtime image (no uv, no dev deps, no tests)
# ======================================================

FROM base AS prod

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    # `setup_logging()` adds a loguru sink at the relative path "app.log", so the
    # working directory has to be writable by the runtime user.
    && chown app:app /app

COPY --from=deps --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app

# The application's own defaults are development-oriented — `db_echo` defaults to
# True, which logs every statement. Override them here; any of these can still be
# replaced at `docker run` time.
ENV DB_ECHO=false \
    LOG_LEVEL=INFO \
    LOG_DIAGNOSE=false

# DATABASE_URL has no default in `Settings` and `.env` is excluded by
# .dockerignore, so it must be supplied at runtime:
#
#   docker run -e DATABASE_URL=postgresql+asyncpg://user:pass@host/db \
#              -e REDIS_URL=redis://redis:6379/0 -p 8000:8000 fastapi-app

USER app

EXPOSE 8000

# /health is pure liveness — it touches neither the database nor Redis.
# Use /health/ready for an orchestrator readiness probe instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

# Single process: the lifespan builds one connection pool per process, so scale
# with replicas rather than `--workers` unless the pool sizes are lowered to match.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
