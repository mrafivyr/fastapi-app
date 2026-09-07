"""Tests for `app/schemas/user.py`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate


# ======================================================
# USER CREATE
# ======================================================


def test_user_create_accepts_a_valid_payload() -> None:
    data = UserCreate(name="Alice", email="alice@example.com")

    assert data.name == "Alice"
    assert data.email == "alice@example.com"


@pytest.mark.parametrize(
    "email",
    ["not-an-email", "alice@", "@example.com", "alice example.com", ""],
)
def test_user_create_rejects_invalid_emails(email: str) -> None:
    with pytest.raises(ValidationError):
        UserCreate(name="Alice", email=email)


def test_user_create_requires_name_and_email() -> None:
    with pytest.raises(ValidationError):
        UserCreate(name="Alice")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        UserCreate(email="alice@example.com")  # type: ignore[call-arg]


def test_user_create_rejects_a_null_name() -> None:
    with pytest.raises(ValidationError):
        UserCreate(name=None, email="alice@example.com")  # type: ignore[arg-type]


# ======================================================
# USER UPDATE
# ======================================================


def test_user_update_fields_are_all_optional() -> None:
    data = UserUpdate()

    assert data.name is None
    assert data.email is None


def test_user_update_exclude_unset_reports_only_supplied_fields() -> None:
    assert UserUpdate(name="Alice").model_dump(exclude_unset=True) == {"name": "Alice"}
    assert UserUpdate().model_dump(exclude_unset=True) == {}


def test_user_update_explicit_none_is_considered_set() -> None:
    """Worth knowing: an explicit `null` would be written to a NOT NULL column."""
    assert UserUpdate(name=None).model_dump(exclude_unset=True) == {"name": None}


def test_user_update_still_validates_email() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(email="nope")


# ======================================================
# USER RESPONSE
# ======================================================


def test_user_response_reads_from_orm_attributes() -> None:
    created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    user = User(id=1, name="Alice", email="alice@example.com", created_at=created_at)

    response = UserResponse.model_validate(user)

    assert response.id == 1
    assert response.name == "Alice"
    assert response.email == "alice@example.com"
    assert response.created_at == created_at


def test_user_response_json_round_trip() -> None:
    original = UserResponse(
        id=1,
        name="Alice",
        email="alice@example.com",
        created_at=datetime(2024, 1, 1),
    )

    restored = UserResponse.model_validate_json(original.model_dump_json())

    assert restored == original


def test_user_response_parses_the_cached_shape_written_by_the_service() -> None:
    """`UserService.create_user` hand-builds this dict; `get_user` reads it back
    with `model_validate_json`, so the two must stay compatible."""
    cached = (
        '{"id": 1, "name": "Alice", "email": "alice@example.com", '
        '"created_at": "2024-01-01T00:00:00+00:00"}'
    )

    response = UserResponse.model_validate_json(cached)

    assert response.id == 1
    assert response.created_at == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_user_response_requires_created_at() -> None:
    with pytest.raises(ValidationError):
        UserResponse(id=1, name="Alice", email="alice@example.com")  # type: ignore[call-arg]
