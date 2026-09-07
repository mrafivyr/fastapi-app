"""Tests for `app/routers/external.py`."""

from __future__ import annotations

import httpx

from tests.fakes import EXTERNAL_USERS_PAYLOAD, MockHTTP


EXTERNAL_URL = "https://jsonplaceholder.typicode.com/users"


async def test_returns_upstream_payload(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    response = await client.get("/external/users")

    assert response.status_code == 200
    assert response.json() == EXTERNAL_USERS_PAYLOAD


async def test_calls_the_expected_upstream_url(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    await client.get("/external/users")

    assert len(mock_http.requests) == 1
    assert mock_http.last_url == EXTERNAL_URL
    assert mock_http.requests[0].method == "GET"


async def test_upstream_5xx_becomes_502(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    mock_http.respond_with(503, json={"detail": "unavailable"})

    response = await client.get("/external/users")

    assert response.status_code == 502
    assert "External service error" in response.json()["detail"]


async def test_upstream_4xx_becomes_502(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    mock_http.respond_with(404, json={"detail": "not found"})

    response = await client.get("/external/users")

    assert response.status_code == 502


async def test_connect_error_becomes_502(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    mock_http.raise_error(httpx.ConnectError("dns failure"))

    response = await client.get("/external/users")

    assert response.status_code == 502
    assert "dns failure" in response.json()["detail"]


async def test_timeout_becomes_502(
    client: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    mock_http.raise_error(httpx.ReadTimeout("too slow"))

    response = await client.get("/external/users")

    assert response.status_code == 502


async def test_non_http_error_is_not_swallowed(
    client_capturing_errors: httpx.AsyncClient,
    mock_http: MockHTTP,
) -> None:
    """Only `httpx.HTTPError` maps to 502; anything else is a real 500."""
    mock_http.raise_error(ValueError("bug in the handler"))

    response = await client_capturing_errors.get("/external/users")

    assert response.status_code == 500
