"""Tests for `app/services/user.py` — the commit/cache orchestration layer."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.user import UserService

from tests.fakes import FakeRedis


CACHE_TTL_SECONDS = 300


# ======================================================
# CREATE
# ======================================================


async def test_create_user_commits(
    user_service: UserService,
    session_factory: async_sessionmaker,
) -> None:
    user = await user_service.create_user(
        UserCreate(name="Alice", email="alice@example.com")
    )

    async with session_factory() as other_session:
        persisted = await UserRepository(other_session).get_by_id(user.id)

    assert persisted is not None
    assert persisted.email == "alice@example.com"


async def test_create_user_warms_the_cache_with_a_ttl(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    user = await user_service.create_user(
        UserCreate(name="Alice", email="alice@example.com")
    )

    key = f"user:{user.id}"
    cached = json.loads(fake_redis.raw(key))

    assert cached == {
        "id": user.id,
        "name": "Alice",
        "email": "alice@example.com",
        "created_at": user.created_at.isoformat(),
    }
    assert fake_redis.ttl_of(key) == CACHE_TTL_SECONDS


async def test_create_user_propagates_duplicate_email_as_value_error(
    user_service: UserService,
) -> None:
    payload = UserCreate(name="Alice", email="alice@example.com")

    await user_service.create_user(payload)

    with pytest.raises(ValueError, match="User with this email already exists"):
        await user_service.create_user(payload)


async def test_create_user_does_not_cache_on_failure(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    payload = UserCreate(name="Alice", email="alice@example.com")

    await user_service.create_user(payload)
    calls_before = len(fake_redis.set_calls)

    with pytest.raises(ValueError):
        await user_service.create_user(payload)

    assert len(fake_redis.set_calls) == calls_before


# ======================================================
# GET (CACHE-ASIDE)
# ======================================================


async def test_get_user_cache_hit_skips_the_database(
    user_service: UserService,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis.seed(
        "user:7",
        json.dumps(
            {
                "id": 7,
                "name": "From Cache",
                "email": "cached@example.com",
                "created_at": "2024-01-01T00:00:00",
            }
        ),
    )

    async def _explode(*_args, **_kwargs):
        raise AssertionError("the database must not be queried on a cache hit")

    monkeypatch.setattr(user_service.repository, "get_by_id", _explode)

    result = await user_service.get_user(7)

    assert isinstance(result, UserResponse)
    assert result.name == "From Cache"


async def test_get_user_cache_miss_reads_db_and_caches(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    user = await user_service.create_user(
        UserCreate(name="Alice", email="alice@example.com")
    )

    key = f"user:{user.id}"
    await fake_redis.delete(key)

    result = await user_service.get_user(user.id)

    assert isinstance(result, UserResponse)
    assert result.email == "alice@example.com"
    assert json.loads(fake_redis.raw(key))["email"] == "alice@example.com"
    assert fake_redis.ttl_of(key) == CACHE_TTL_SECONDS


async def test_get_user_returns_none_when_missing(
    user_service: UserService,
) -> None:
    assert await user_service.get_user(12345) is None


async def test_get_user_does_not_cache_a_miss(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    await user_service.get_user(12345)

    assert fake_redis.raw("user:12345") is None


async def test_get_user_expired_cache_falls_back_to_db(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    user = await user_service.create_user(
        UserCreate(name="Alice", email="alice@example.com")
    )

    # Simulate expiry rather than waiting for it.
    fake_redis.store.pop(f"user:{user.id}")

    result = await user_service.get_user(user.id)

    assert result is not None
    assert result.email == "alice@example.com"


# ======================================================
# UPDATE
# ======================================================


async def test_update_user_commits_and_invalidates_cache(
    user_service: UserService,
    fake_redis: FakeRedis,
    session_factory: async_sessionmaker,
) -> None:
    user = await user_service.create_user(
        UserCreate(name="Alice", email="alice@example.com")
    )

    updated = await user_service.update_user(user.id, UserUpdate(name="Renamed"))

    assert updated is not None
    assert updated.name == "Renamed"
    assert f"user:{user.id}" in fake_redis.deleted_keys
    assert fake_redis.raw(f"user:{user.id}") is None

    async with session_factory() as other_session:
        persisted = await UserRepository(other_session).get_by_id(user.id)

    assert persisted is not None
    assert persisted.name == "Renamed"


async def test_update_user_missing_returns_none_without_touching_cache(
    user_service: UserService,
    fake_redis: FakeRedis,
) -> None:
    result = await user_service.update_user(12345, UserUpdate(name="Ghost"))

    assert result is None
    assert fake_redis.deleted_keys == []


async def test_update_user_propagates_duplicate_email_as_value_error(
    user_service: UserService,
) -> None:
    await user_service.create_user(UserCreate(name="Alice", email="alice@example.com"))
    bob = await user_service.create_user(UserCreate(name="Bob", email="bob@example.com"))

    with pytest.raises(ValueError, match="User with this email already exists"):
        await user_service.update_user(
            bob.id,
            UserUpdate(email="alice@example.com"),
        )


# ======================================================
# LIST
# ======================================================


async def test_get_users_empty(user_service: UserService) -> None:
    assert await user_service.get_users() == []


async def test_get_users_returns_all_rows(user_service: UserService) -> None:
    await user_service.create_user(UserCreate(name="Alice", email="alice@example.com"))
    await user_service.create_user(UserCreate(name="Bob", email="bob@example.com"))

    users = await user_service.get_users()

    assert {user.email for user in users} == {
        "alice@example.com",
        "bob@example.com",
    }
