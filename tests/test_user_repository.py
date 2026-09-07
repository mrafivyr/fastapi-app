"""Tests for `app/repositories/user.py`.

These talk to a real (in-memory SQLite) session, so the SQL is genuinely
executed; only the surrounding process is a test one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserUpdate


@pytest.fixture
def repository(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)


async def _seed(
    repository: UserRepository,
    name: str = "Alice",
    email: str = "alice@example.com",
) -> User:
    return await repository.create(UserCreate(name=name, email=email))


# ======================================================
# CREATE
# ======================================================


async def test_create_assigns_id_and_created_at(repository: UserRepository) -> None:
    user = await _seed(repository)

    assert user.id is not None
    assert user.name == "Alice"
    assert user.email == "alice@example.com"
    assert user.created_at is not None


async def test_create_flushes_so_the_row_is_queryable(
    repository: UserRepository,
) -> None:
    user = await _seed(repository)

    assert await repository.get_by_id(user.id) is user


async def test_create_does_not_commit(
    repository: UserRepository,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create` only flushes — committing is the service's job."""
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)

    await _seed(repository)

    commit.assert_not_awaited()
    assert db_session.in_transaction()


async def test_create_rejects_a_duplicate_email(repository: UserRepository) -> None:
    await _seed(repository)

    with pytest.raises(ValueError, match="User with this email already exists"):
        await _seed(repository, name="Impostor")


async def test_create_allows_a_duplicate_name(repository: UserRepository) -> None:
    await _seed(repository, name="Alice", email="alice1@example.com")
    second = await _seed(repository, name="Alice", email="alice2@example.com")

    assert second.id is not None


# ======================================================
# GET BY ID / EMAIL
# ======================================================


async def test_get_by_id_returns_the_row(repository: UserRepository) -> None:
    user = await _seed(repository)

    found = await repository.get_by_id(user.id)

    assert found is not None
    assert found.email == "alice@example.com"


async def test_get_by_id_returns_none_when_absent(
    repository: UserRepository,
) -> None:
    assert await repository.get_by_id(12345) is None


async def test_get_by_email_returns_the_row(repository: UserRepository) -> None:
    await _seed(repository)

    found = await repository.get_by_email("alice@example.com")

    assert found is not None
    assert found.name == "Alice"


async def test_get_by_email_returns_none_when_absent(
    repository: UserRepository,
) -> None:
    assert await repository.get_by_email("nobody@example.com") is None


async def test_get_by_email_with_none_returns_none(
    repository: UserRepository,
) -> None:
    """`update()` calls this with `None` for a partial payload — the column is
    NOT NULL, so no row can ever match."""
    await _seed(repository)

    assert await repository.get_by_email(None) is None


# ======================================================
# GET ALL
# ======================================================


async def test_get_all_empty(repository: UserRepository) -> None:
    assert await repository.get_all() == []


async def test_get_all_returns_a_list_of_users(repository: UserRepository) -> None:
    await _seed(repository, "Alice", "alice@example.com")
    await _seed(repository, "Bob", "bob@example.com")

    users = await repository.get_all()

    assert isinstance(users, list)
    assert {user.email for user in users} == {
        "alice@example.com",
        "bob@example.com",
    }


# ======================================================
# UPDATE
# ======================================================


async def test_update_applies_both_fields(repository: UserRepository) -> None:
    user = await _seed(repository)

    updated = await repository.update(
        user.id,
        UserUpdate(name="Alice II", email="alice2@example.com"),
    )

    assert updated is not None
    assert updated.name == "Alice II"
    assert updated.email == "alice2@example.com"


async def test_update_only_touches_provided_fields(
    repository: UserRepository,
) -> None:
    user = await _seed(repository, "Alice", "alice@example.com")

    updated = await repository.update(user.id, UserUpdate(name="Renamed"))

    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.email == "alice@example.com"


async def test_update_with_an_empty_payload_changes_nothing(
    repository: UserRepository,
) -> None:
    user = await _seed(repository, "Alice", "alice@example.com")

    updated = await repository.update(user.id, UserUpdate())

    assert updated is not None
    assert updated.name == "Alice"
    assert updated.email == "alice@example.com"


async def test_update_returns_none_for_a_missing_user(
    repository: UserRepository,
) -> None:
    assert await repository.update(12345, UserUpdate(name="Ghost")) is None


async def test_update_rejects_an_email_owned_by_someone_else(
    repository: UserRepository,
) -> None:
    await _seed(repository, "Alice", "alice@example.com")
    bob = await _seed(repository, "Bob", "bob@example.com")

    with pytest.raises(ValueError, match="User with this email already exists"):
        await repository.update(bob.id, UserUpdate(email="alice@example.com"))


async def test_update_allows_reassigning_its_own_email(
    repository: UserRepository,
) -> None:
    user = await _seed(repository, "Alice", "alice@example.com")

    updated = await repository.update(
        user.id,
        UserUpdate(name="Alice II", email="alice@example.com"),
    )

    assert updated is not None
    assert updated.name == "Alice II"


async def test_update_does_not_commit(
    repository: UserRepository,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed(repository)

    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)

    await repository.update(user.id, UserUpdate(name="Renamed"))

    commit.assert_not_awaited()
