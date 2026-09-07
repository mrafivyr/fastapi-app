"""Tests for `app/routers/users.py` (CRUD + cache endpoint)."""

from __future__ import annotations

import json

import httpx

from tests.fakes import FakeRedis


# ======================================================
# CREATE
# ======================================================


async def test_create_user_returns_201_with_body(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/users/",
        json={"name": "Alice", "email": "alice@example.com"},
    )

    assert response.status_code == 201

    body = response.json()

    assert body["id"] > 0
    assert body["name"] == "Alice"
    assert body["email"] == "alice@example.com"
    assert body["created_at"]


async def test_create_user_response_has_no_extra_fields(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/users/",
        json={"name": "Alice", "email": "alice@example.com"},
    )

    assert set(response.json()) == {"id", "name", "email", "created_at"}


async def test_create_user_warms_the_cache(
    client: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    body = (
        await client.post(
            "/users/",
            json={"name": "Alice", "email": "alice@example.com"},
        )
    ).json()

    key = f"user:{body['id']}"
    cached = json.loads(fake_redis.raw(key))

    assert cached["id"] == body["id"]
    assert cached["name"] == "Alice"
    assert cached["email"] == "alice@example.com"
    assert fake_redis.ttl_of(key) == 300


async def test_create_user_persists_to_the_database(
    client: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    created = (
        await client.post(
            "/users/",
            json={"name": "Alice", "email": "alice@example.com"},
        )
    ).json()

    # Drop the cache so the read has to go to the database.
    await fake_redis.delete(f"user:{created['id']}")

    response = await client.get(f"/users/{created['id']}")

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


async def test_create_user_duplicate_email_returns_409(
    client: httpx.AsyncClient,
) -> None:
    payload = {"name": "Alice", "email": "alice@example.com"}

    assert (await client.post("/users/", json=payload)).status_code == 201

    response = await client.post("/users/", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "User with this email already exists"


async def test_create_user_duplicate_email_creates_no_second_row(
    client: httpx.AsyncClient,
) -> None:
    payload = {"name": "Alice", "email": "alice@example.com"}

    await client.post("/users/", json=payload)
    await client.post("/users/", json={**payload, "name": "Impostor"})

    listed = (await client.get("/users/")).json()

    assert len(listed) == 1


async def test_create_user_invalid_email_returns_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/users/",
        json={"name": "Alice", "email": "not-an-email"},
    )

    assert response.status_code == 422


async def test_create_user_missing_name_returns_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/users/", json={"email": "alice@example.com"})

    assert response.status_code == 422


async def test_create_user_empty_body_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/users/", json={})

    assert response.status_code == 422


async def test_create_user_ignores_client_supplied_id(
    client: httpx.AsyncClient,
) -> None:
    """`UserCreate` has no `id`, so an injected one must not be honoured."""
    response = await client.post(
        "/users/",
        json={"id": 999, "name": "Alice", "email": "alice@example.com"},
    )

    assert response.status_code == 201
    assert response.json()["id"] != 999


# ======================================================
# GET ONE
# ======================================================


async def test_get_user_served_from_cache(
    client: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    """A cached entry is returned without consulting the database.

    Nothing is written to the DB here, so a response can only come from Redis.
    """
    fake_redis.seed(
        "user:42",
        json.dumps(
            {
                "id": 42,
                "name": "From Cache",
                "email": "cached@example.com",
                "created_at": "2024-01-01T00:00:00",
            }
        ),
    )

    response = await client.get("/users/42")

    assert response.status_code == 200
    assert response.json()["name"] == "From Cache"


async def test_get_user_cache_miss_repopulates_cache(
    client: httpx.AsyncClient,
    create_user,
    fake_redis: FakeRedis,
) -> None:
    created = await create_user()
    key = f"user:{created['id']}"

    await fake_redis.delete(key)
    assert fake_redis.raw(key) is None

    response = await client.get(f"/users/{created['id']}")

    assert response.status_code == 200
    assert json.loads(fake_redis.raw(key))["email"] == created["email"]
    assert fake_redis.ttl_of(key) == 300


async def test_get_user_not_found_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/users/12345")

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


async def test_get_user_not_found_does_not_cache_a_miss(
    client: httpx.AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    await client.get("/users/12345")

    assert fake_redis.raw("user:12345") is None


async def test_get_user_non_integer_id_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/users/not-a-number")

    assert response.status_code == 422


# ======================================================
# LIST
# ======================================================


async def test_list_users_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/users/")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_users_returns_every_row(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    await create_user("Alice", "alice@example.com")
    await create_user("Bob", "bob@example.com")

    response = await client.get("/users/")

    assert response.status_code == 200

    emails = {user["email"] for user in response.json()}

    assert emails == {"alice@example.com", "bob@example.com"}


async def test_list_users_does_not_read_the_cache(
    client: httpx.AsyncClient,
    create_user,
    fake_redis: FakeRedis,
) -> None:
    """The list endpoint goes straight to the DB, so a stale cache is ignored."""
    created = await create_user("Alice", "alice@example.com")

    fake_redis.seed(
        f"user:{created['id']}",
        json.dumps(
            {
                "id": created["id"],
                "name": "Stale",
                "email": "stale@example.com",
                "created_at": "2024-01-01T00:00:00",
            }
        ),
    )

    listed = (await client.get("/users/")).json()

    assert listed[0]["name"] == "Alice"


# ======================================================
# UPDATE
# ======================================================


async def test_update_user_changes_both_fields(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user()

    response = await client.put(
        f"/users/{created['id']}",
        json={"name": "Alice Updated", "email": "alice.updated@example.com"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == created["id"]
    assert body["name"] == "Alice Updated"
    assert body["email"] == "alice.updated@example.com"


async def test_update_user_invalidates_the_cache(
    client: httpx.AsyncClient,
    create_user,
    fake_redis: FakeRedis,
) -> None:
    created = await create_user()
    key = f"user:{created['id']}"

    assert fake_redis.raw(key) is not None

    await client.put(f"/users/{created['id']}", json={"name": "Alice Updated"})

    assert key in fake_redis.deleted_keys
    assert fake_redis.raw(key) is None


async def test_update_user_next_read_sees_new_value(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user()

    await client.put(f"/users/{created['id']}", json={"name": "Alice Updated"})

    response = await client.get(f"/users/{created['id']}")

    assert response.json()["name"] == "Alice Updated"


async def test_update_user_partial_leaves_other_fields_alone(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user("Alice", "alice@example.com")

    response = await client.put(
        f"/users/{created['id']}",
        json={"name": "Renamed"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["email"] == "alice@example.com"


async def test_update_user_email_only(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user("Alice", "alice@example.com")

    response = await client.put(
        f"/users/{created['id']}",
        json={"email": "new@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Alice"
    assert response.json()["email"] == "new@example.com"


async def test_update_user_empty_payload_is_a_no_op(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user("Alice", "alice@example.com")

    response = await client.put(f"/users/{created['id']}", json={})

    assert response.status_code == 200
    assert response.json() == created


async def test_update_user_keeping_its_own_email_is_allowed(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user("Alice", "alice@example.com")

    response = await client.put(
        f"/users/{created['id']}",
        json={"name": "Alice II", "email": "alice@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Alice II"


async def test_update_user_to_a_taken_email_returns_409(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    await create_user("Alice", "alice@example.com")
    bob = await create_user("Bob", "bob@example.com")

    response = await client.put(
        f"/users/{bob['id']}",
        json={"email": "alice@example.com"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "User with this email already exists"


async def test_update_user_not_found_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.put("/users/12345", json={"name": "Ghost"})

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


async def test_update_user_invalid_email_returns_422(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user()

    response = await client.put(
        f"/users/{created['id']}",
        json={"email": "nope"},
    )

    assert response.status_code == 422


async def test_update_user_non_integer_id_returns_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put("/users/abc", json={"name": "Ghost"})

    assert response.status_code == 422


# ======================================================
# CACHE INSPECTION ENDPOINT
# ======================================================


async def test_cache_endpoint_returns_the_raw_cached_value(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user()

    response = await client.get(f"/users/{created['id']}/cache")

    assert response.status_code == 200

    body = response.json()

    assert body["key"] == f"user:{created['id']}"
    assert json.loads(body["value"])["email"] == created["email"]


async def test_cache_endpoint_returns_null_for_a_missing_key(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/users/12345/cache")

    assert response.status_code == 200
    assert response.json() == {"key": "user:12345", "value": None}


async def test_cache_endpoint_reflects_invalidation(
    client: httpx.AsyncClient,
    create_user,
) -> None:
    created = await create_user()

    await client.put(f"/users/{created['id']}", json={"name": "Alice Updated"})

    response = await client.get(f"/users/{created['id']}/cache")

    assert response.json()["value"] is None
