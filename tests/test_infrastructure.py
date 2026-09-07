"""Tests for `app/config.py` and the `app/infrastructure/` factories."""

from __future__ import annotations

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings, settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.infrastructure.http_client import create_http_client
from app.infrastructure.redis import create_redis_client


# ======================================================
# SETTINGS
# ======================================================


def test_settings_read_the_environment() -> None:
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert settings.http_timeout == 5.0
    assert settings.db_echo is False
    assert settings.log_level == "CRITICAL"


def test_settings_require_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # `.env` in the project root also supplies DATABASE_URL, so point the
    # loader at a file that does not exist to prove the field is mandatory.
    with pytest.raises(Exception):
        Settings(_env_file="does-not-exist.env")  # type: ignore[call-arg]


def test_settings_declared_defaults() -> None:
    """Checked against the field declarations, since the test environment
    deliberately overrides several of these."""
    fields = Settings.model_fields

    assert fields["db_pool_size"].default == 10
    assert fields["db_max_overflow"].default == 20
    assert fields["db_pool_timeout"].default == 30
    assert fields["db_pool_recycle"].default == 1800
    assert fields["redis_url"].default == "redis://localhost:6379/0"
    assert fields["http_timeout"].default == 10.0
    assert fields["log_level"].default == "INFO"
    assert fields["log_diagnose"].default is False
    assert fields["db_echo"].default is True
    assert fields["slow_query_threshold_ms"].default == 100


def test_database_url_is_the_only_required_setting() -> None:
    assert Settings.model_fields["database_url"].is_required()

    optional = {
        name
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }

    assert optional == {"database_url"}


def test_settings_ignore_unknown_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOMETHING_UNRELATED", "value")

    assert Settings().database_url == settings.database_url


# ======================================================
# DATABASE
# ======================================================


async def test_create_db_engine_returns_a_usable_engine() -> None:
    engine = create_db_engine()

    try:
        assert isinstance(engine, AsyncEngine)
        assert str(engine.url) == settings.database_url
        assert engine.echo is False
    finally:
        await engine.dispose()


def test_create_db_engine_forwards_pool_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_create_async_engine(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return "engine-sentinel"

    monkeypatch.setattr(
        "app.infrastructure.database.create_async_engine",
        fake_create_async_engine,
    )

    assert create_db_engine() == "engine-sentinel"
    assert captured["url"] == settings.database_url
    assert captured["pool_size"] == settings.db_pool_size
    assert captured["max_overflow"] == settings.db_max_overflow
    assert captured["pool_timeout"] == settings.db_pool_timeout
    assert captured["pool_recycle"] == settings.db_pool_recycle
    assert captured["pool_pre_ping"] is True
    assert captured["echo"] == settings.db_echo


async def test_create_session_factory_configuration(db_engine: AsyncEngine) -> None:
    factory = create_session_factory(db_engine)

    async with factory() as session:
        assert isinstance(session, AsyncSession)
        assert session.bind is db_engine
        assert session.sync_session.expire_on_commit is False
        assert session.sync_session.autoflush is False


# ======================================================
# REDIS
# ======================================================


async def test_create_redis_client_configuration() -> None:
    client = create_redis_client()

    try:
        assert isinstance(client, Redis)

        connection_kwargs = client.connection_pool.connection_kwargs

        assert connection_kwargs["decode_responses"] is True
        assert connection_kwargs["encoding"] == "utf-8"
    finally:
        await client.aclose()


async def test_create_redis_client_uses_the_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://example.test:6380/3")

    client = create_redis_client()

    try:
        connection_kwargs = client.connection_pool.connection_kwargs

        assert connection_kwargs["host"] == "example.test"
        assert connection_kwargs["port"] == 6380
        assert connection_kwargs["db"] == 3
    finally:
        await client.aclose()


# ======================================================
# HTTP CLIENT
# ======================================================


async def test_create_http_client_configuration() -> None:
    client = create_http_client()

    try:
        assert isinstance(client, httpx.AsyncClient)
        assert client.follow_redirects is True
        assert client.timeout.connect == settings.http_timeout
        assert client.timeout.read == settings.http_timeout
    finally:
        await client.aclose()


async def test_create_http_client_is_not_closed_on_creation() -> None:
    client = create_http_client()

    try:
        assert client.is_closed is False
    finally:
        await client.aclose()

    assert client.is_closed is True
