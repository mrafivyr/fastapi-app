"""Tests for `app/lifespan.py`.

The lifespan never *connects* to Redis (it only constructs the client and
closes it), and `settings.database_url` points at a throwaway SQLite file, so
the real startup/shutdown sequence can be exercised end to end.
"""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

import app.lifespan as lifespan_module
from app.lifespan import lifespan
from app.main import app as real_app

from tests.fakes import FakeRedis


# ======================================================
# STARTUP
# ======================================================


async def test_startup_populates_app_state() -> None:
    test_app = FastAPI()

    async with lifespan(test_app):
        assert isinstance(test_app.state.db_engine, AsyncEngine)
        assert test_app.state.db_session_factory is not None
        assert test_app.state.redis is not None
        assert isinstance(test_app.state.http_client, httpx.AsyncClient)


async def test_startup_creates_the_users_table() -> None:
    test_app = FastAPI()

    async with lifespan(test_app):
        async with test_app.state.db_engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: sqlalchemy.inspect(
                    sync_connection
                ).get_table_names()
            )

    assert "users" in tables


async def test_startup_session_factory_produces_working_sessions() -> None:
    test_app = FastAPI()

    async with lifespan(test_app):
        async with test_app.state.db_session_factory() as session:
            result = await session.execute(sqlalchemy.text("SELECT 1"))

            assert result.scalar_one() == 1


# ======================================================
# SHUTDOWN
# ======================================================


async def test_shutdown_closes_every_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        lifespan_module,
        "create_redis_client",
        lambda: fake_redis,
    )

    test_app = FastAPI()

    async with lifespan(test_app):
        http_client = test_app.state.http_client

        assert http_client.is_closed is False
        assert fake_redis.closed is False

    assert http_client.is_closed is True
    assert fake_redis.closed is True


async def test_shutdown_runs_even_when_the_app_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup lives in a `finally`, so a crash must not leak connections."""
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        lifespan_module,
        "create_redis_client",
        lambda: fake_redis,
    )

    test_app = FastAPI()

    with pytest.raises(RuntimeError, match="boom"):
        async with lifespan(test_app):
            http_client = test_app.state.http_client
            raise RuntimeError("boom")

    assert http_client.is_closed is True
    assert fake_redis.closed is True


# ======================================================
# SMOKE TEST THROUGH THE REAL APP
# ======================================================


def test_real_app_boots_and_serves_health() -> None:
    """`TestClient` as a context manager runs the actual lifespan."""
    with TestClient(real_app) as test_client:
        response = test_client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "X-Request-ID" in response.headers
