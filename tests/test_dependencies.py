"""Tests for `app/dependencies.py`.

The dependency callables only read `request.app.state`, so a lightweight
stand-in is enough — no ASGI round trip needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_http_client,
    get_redis,
    get_user_service,
)
from app.services.user import UserService

from tests.fakes import FakeRedis


def make_request(**state: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


# ======================================================
# DATABASE SESSION
# ======================================================


async def test_get_db_yields_a_usable_session(session_factory) -> None:
    request = make_request(db_session_factory=session_factory)

    generator = get_db(request)
    session = await anext(generator)

    assert isinstance(session, AsyncSession)
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    with pytest.raises(StopAsyncIteration):
        await anext(generator)


async def test_get_db_closes_the_session_on_exit(session_factory) -> None:
    request = make_request(db_session_factory=session_factory)

    generator = get_db(request)
    session = await anext(generator)

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert not session.is_active or not session.in_transaction()


async def test_get_db_rolls_back_and_reraises_on_error(session_factory) -> None:
    request = make_request(db_session_factory=session_factory)

    generator = get_db(request)
    session = await anext(generator)

    session.rollback = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await generator.athrow(RuntimeError("boom"))

    session.rollback.assert_awaited_once()


async def test_get_db_does_not_commit_for_you(session_factory) -> None:
    """Committing is left to the service layer."""
    request = make_request(db_session_factory=session_factory)

    generator = get_db(request)
    session = await anext(generator)
    session.commit = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    session.commit.assert_not_awaited()


# ======================================================
# REDIS / HTTP CLIENT
# ======================================================


def test_get_redis_returns_the_shared_client(fake_redis: FakeRedis) -> None:
    request = make_request(redis=fake_redis)

    assert get_redis(request) is fake_redis


def test_get_http_client_returns_the_shared_client() -> None:
    client = httpx.AsyncClient()
    request = make_request(http_client=client)

    assert get_http_client(request) is client


# ======================================================
# SERVICE WIRING
# ======================================================


async def test_get_user_service_wires_db_and_redis(
    db_session: AsyncSession,
    fake_redis: FakeRedis,
) -> None:
    service = get_user_service(db=db_session, redis=fake_redis)  # type: ignore[arg-type]

    assert isinstance(service, UserService)
    assert service.db is db_session
    assert service.redis is fake_redis
    assert service.repository.db is db_session
