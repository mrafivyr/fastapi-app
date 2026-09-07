"""Test doubles for the infrastructure the app talks to.

`FakeRedis` implements only the surface the application actually uses
(`get`, `set`, `delete`, `ping`, `exists`, `aclose`) and mirrors the real
client's `decode_responses=True` behaviour by storing/returning `str`.

`MockHTTP` wraps `httpx.MockTransport` so tests can swap the response for the
outbound call in `app/routers/external.py` without touching the network.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx


# ======================================================
# REDIS
# ======================================================


class FakeRedis:
    """In-memory stand-in for `redis.asyncio.Redis`."""

    def __init__(self) -> None:
        # key -> (value, expires_at_monotonic | None)
        self.store: dict[str, tuple[str, float | None]] = {}
        # Recorded calls, for assertions.
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.deleted_keys: list[str] = []
        self.ping_count = 0
        self.closed = False
        # Failure injection.
        self.fail_ping: BaseException | None = None

    # -- helpers used by tests only -----------------------------

    def seed(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, time.monotonic() + ex if ex else None)

    def raw(self, key: str) -> str | None:
        entry = self.store.get(key)
        return None if entry is None else entry[0]

    def ttl_of(self, key: str) -> int | None:
        """The `ex` value the application passed for `key`, if any."""
        for called_key, _value, ex in reversed(self.set_calls):
            if called_key == key:
                return ex
        return None

    # -- redis surface ------------------------------------------

    async def get(self, key: str) -> str | None:
        entry = self.store.get(key)

        if entry is None:
            return None

        value, expires_at = entry

        if expires_at is not None and time.monotonic() >= expires_at:
            del self.store[key]
            return None

        return value

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        **_kwargs: Any,
    ) -> bool:
        if isinstance(value, bytes):
            value = value.decode()

        self.store[key] = (str(value), time.monotonic() + ex if ex else None)
        self.set_calls.append((key, str(value), ex))

        return True

    async def delete(self, *keys: str) -> int:
        removed = 0

        for key in keys:
            self.deleted_keys.append(key)

            if self.store.pop(key, None) is not None:
                removed += 1

        return removed

    async def exists(self, *keys: str) -> int:
        return sum(1 for key in keys if key in self.store)

    async def ping(self) -> bool:
        self.ping_count += 1

        if self.fail_ping is not None:
            raise self.fail_ping

        return True

    async def aclose(self) -> None:
        self.closed = True


# ======================================================
# HTTP CLIENT
# ======================================================


EXTERNAL_USERS_PAYLOAD = [
    {"id": 1, "name": "Leanne Graham", "email": "Sincere@april.biz"},
    {"id": 2, "name": "Ervin Howell", "email": "Shanna@melissa.tv"},
]


class MockHTTP:
    """Owns a `httpx.MockTransport` whose handler tests can replace."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = (
            lambda _request: httpx.Response(200, json=EXTERNAL_USERS_PAYLOAD)
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            follow_redirects=True,
        )

    # -- convenience configuration ------------------------------

    def respond_with(self, status_code: int, json: Any = None) -> None:
        self.handler = lambda _request: httpx.Response(status_code, json=json)

    def raise_error(self, exc: Exception) -> None:
        def _raise(_request: httpx.Request) -> httpx.Response:
            raise exc

        self.handler = _raise

    @property
    def last_url(self) -> str | None:
        return str(self.requests[-1].url) if self.requests else None
