"""Shared fixtures for the FastAPI application test suite.

Two things shape this file:

1. `app.config.settings` is instantiated at import time, so every environment
   variable the tests need must be set *before* anything under `app.` is
   imported. Hence the `os.environ` block above the app imports.

2. The application reads its infrastructure handles off `app.state`
   (`db_session_factory`, `redis`, `http_client`), which the lifespan normally
   populates. The tests skip the lifespan entirely and put test doubles on
   `app.state` instead, so the real dependency functions in
   `app/dependencies.py` are still exercised while no real Postgres, Redis or
   network is required.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# `create_db_engine()` passes pool_size/max_overflow/pool_timeout, which the
# SQLite *memory* dialect rejects (it defaults to StaticPool). A file-backed
# SQLite URL gets an AsyncAdaptedQueuePool and accepts those arguments, so the
# real engine factory and the real lifespan stay testable.
_SETTINGS_DB_DIR = tempfile.mkdtemp(prefix="fastapi_app_tests_")
SETTINGS_DATABASE_URL = (
    f"sqlite+aiosqlite:///{(Path(_SETTINGS_DB_DIR) / 'settings.db').as_posix()}"
)

os.environ["DATABASE_URL"] = SETTINGS_DATABASE_URL
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["HTTP_TIMEOUT"] = "5"
os.environ["DB_ECHO"] = "false"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_DIAGNOSE"] = "false"

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.main import app  # noqa: E402
from app.models.user import Base  # noqa: E402
from app.services.user import UserService  # noqa: E402
from tests.fakes import FakeRedis, MockHTTP  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_settings_db_dir() -> Iterator[None]:
    yield
    shutil.rmtree(_SETTINGS_DB_DIR, ignore_errors=True)


# ======================================================
# DATABASE
# ======================================================


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory schema per test.

    `StaticPool` keeps a single connection alive so the `:memory:` database
    survives between sessions taken from the factory.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Mirrors `create_session_factory()` but bound to the test engine."""
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


# ======================================================
# REDIS / HTTP DOUBLES
# ======================================================


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def mock_http() -> MockHTTP:
    return MockHTTP()


@pytest.fixture
async def http_client(mock_http: MockHTTP) -> AsyncIterator[httpx.AsyncClient]:
    async with mock_http.build_client() as client:
        yield client


# ======================================================
# SERVICE
# ======================================================


@pytest.fixture
def user_service(db_session: AsyncSession, fake_redis: FakeRedis) -> UserService:
    return UserService(db=db_session, redis=fake_redis)  # type: ignore[arg-type]


# ======================================================
# HTTP CLIENTS AGAINST THE APP
# ======================================================


@pytest.fixture
def wired_app(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
    http_client: httpx.AsyncClient,
):
    """The real `app` with test doubles on `app.state` (lifespan not run)."""
    app.state.db_engine = None
    app.state.db_session_factory = session_factory
    app.state.redis = fake_redis
    app.state.http_client = http_client

    yield app

    app.dependency_overrides.clear()


@pytest.fixture
async def client(wired_app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=wired_app),
        base_url="http://test",
    ) as async_client:
        yield async_client


@pytest.fixture
async def client_capturing_errors(wired_app) -> AsyncIterator[httpx.AsyncClient]:
    """Like `client`, but unhandled endpoint exceptions become 500 responses
    instead of propagating into the test."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=wired_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as async_client:
        yield async_client


# ======================================================
# HELPERS
# ======================================================


@pytest.fixture
def create_user(client: httpx.AsyncClient):
    """Create a user through the API and return the response body."""

    async def _create(name: str = "Alice", email: str = "alice@example.com") -> dict:
        response = await client.post(
            "/users/",
            json={"name": name, "email": email},
        )

        assert response.status_code == 201, response.text

        return response.json()

    return _create
