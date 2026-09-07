"""Tests for `app/routers/health.py`."""

from __future__ import annotations

import httpx

from tests.fakes import FakeRedis


# ======================================================
# LIVENESS
# ======================================================


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_needs_no_infrastructure(client: httpx.AsyncClient) -> None:
    """Liveness must not touch the DB or Redis."""
    response = await client.get("/health")

    assert response.status_code == 200


async def test_health_trailing_slash_is_not_registered(
    client: httpx.AsyncClient,
) -> None:
    """The route is declared as `""`, so `/health/` is a redirect, not a 200."""
    response = await client.get("/health/")

    assert response.status_code in (307, 404)


# ======================================================
# READINESS
# ======================================================


async def test_readiness_checks_db_and_redis(
    client: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "redis": "ok",
    }
    assert fake_redis.ping_count == 1


async def test_readiness_fails_when_redis_is_down(
    client_capturing_errors: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    """There is no graceful degradation: a Redis failure surfaces as a 500."""
    fake_redis.fail_ping = ConnectionError("redis unreachable")

    response = await client_capturing_errors.get("/health/ready")

    assert response.status_code == 500


async def test_readiness_fails_when_db_is_down(
    wired_app,
    client_capturing_errors: httpx.AsyncClient,
) -> None:
    from app.dependencies import get_db

    async def broken_db():
        raise ConnectionError("database unreachable")
        yield  # pragma: no cover - makes this an async generator

    wired_app.dependency_overrides[get_db] = broken_db

    response = await client_capturing_errors.get("/health/ready")

    assert response.status_code == 500
