"""Tests for the request-ID middleware and app-level wiring in `app/main.py`."""

from __future__ import annotations

import uuid

import httpx


# ======================================================
# REQUEST ID MIDDLEWARE
# ======================================================


async def test_incoming_request_id_is_echoed_back(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/health",
        headers={"X-Request-ID": "caller-supplied-id"},
    )

    assert response.headers["X-Request-ID"] == "caller-supplied-id"


async def test_request_id_is_generated_when_absent(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")

    generated = response.headers["X-Request-ID"]

    assert uuid.UUID(generated).version == 4


async def test_generated_request_ids_are_unique(client: httpx.AsyncClient) -> None:
    first = (await client.get("/health")).headers["X-Request-ID"]
    second = (await client.get("/health")).headers["X-Request-ID"]

    assert first != second


async def test_request_id_is_present_on_error_responses(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/users/12345")

    assert response.status_code == 404
    assert "X-Request-ID" in response.headers


async def test_request_id_is_present_on_validation_errors(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/users/", json={"name": "no email"})

    assert response.status_code == 422
    assert "X-Request-ID" in response.headers


async def test_middleware_does_not_consume_the_request_body(
    client: httpx.AsyncClient,
) -> None:
    """The middleware awaits `request.body()` before `call_next`; the endpoint
    must still receive it."""
    response = await client.post(
        "/users/",
        json={"name": "Alice", "email": "alice@example.com"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Alice"


async def test_middleware_handles_an_empty_body(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200


# ======================================================
# OPENAPI / DOCS
# ======================================================


async def test_openapi_schema_metadata(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert schema["info"]["title"] == "FastAPI Application"
    assert schema["info"]["version"] == "1.0.0"


async def test_openapi_schema_lists_every_route(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert {
        "/health",
        "/health/ready",
        "/users/",
        "/users/{user_id}",
        "/users/{user_id}/cache",
        "/external/users",
    } <= set(schema["paths"])


async def test_openapi_documents_create_as_201(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert "201" in schema["paths"]["/users/"]["post"]["responses"]


async def test_scalar_route_is_hidden_from_the_schema(
    client: httpx.AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert "/scalar" not in schema["paths"]


async def test_scalar_docs_render(client: httpx.AsyncClient) -> None:
    response = await client.get("/scalar")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


async def test_swagger_docs_render(client: httpx.AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200


async def test_unknown_route_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
