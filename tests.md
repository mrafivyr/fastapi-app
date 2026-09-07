# How the test suite works

`tests/` covers the FastAPI application end to end — routers, services, repository,
dependencies, lifespan, config factories, middleware and logging. Current state:
**140 passed, 1 xfailed** in ~1.5s. No Postgres, Redis or network access required.

## Configuration (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
asyncio_mode = "auto"                          # async def tests need no decorator
asyncio_default_fixture_loop_scope = "function"
log_level = "WARNING"                          # app calls basicConfig(level=0, force=True)
```

`asyncio_mode = "auto"` is why every `async def test_*` in the suite runs without
`@pytest.mark.asyncio`. Each test gets its own event loop.

## `conftest.py` — the two structural tricks

**1. Environment before imports** (`tests/conftest.py:37-62`). `app.config.settings` is
instantiated at import time, so every environment variable the tests need must be set
*before* anything under `app.` is imported — hence the `os.environ` block above the app
imports and all the `# noqa: E402`.

`DATABASE_URL` points at a throwaway file-backed SQLite DB in a temp dir, not `:memory:`,
because `create_db_engine()` passes `pool_size`/`max_overflow`/`pool_timeout`, which
SQLite's *memory* dialect rejects (it defaults to `StaticPool`). A file-backed URL gets an
`AsyncAdaptedQueuePool` and accepts those arguments, so the real engine factory and the
real lifespan stay testable. A session-scoped autouse fixture `rmtree`s that dir at the end.

**2. Test doubles on `app.state`, lifespan skipped** (`conftest.py:153-167`). The
application reads its infrastructure handles off `app.state` (`db_session_factory`,
`redis`, `http_client`), which the lifespan normally populates. The `wired_app` fixture
assigns doubles directly instead.

The payoff: the *real* dependency functions in `app/dependencies.py` still run — `get_db`
really does `request.app.state.db_session_factory()` — while no Postgres, Redis or network
is required. Nothing is faked at the DI layer.

## The fixture graph

```
db_engine            in-memory SQLite, StaticPool, Base.metadata.create_all
  └─ session_factory   async_sessionmaker(expire_on_commit=False, autoflush=False)
       └─ db_session     one AsyncSession

fake_redis     FakeRedis()
mock_http      MockHTTP()
  └─ http_client   httpx.AsyncClient(transport=MockTransport)

user_service   = UserService(db_session, fake_redis)      # service-layer tests

wired_app      = real `app` + {session_factory, fake_redis, http_client} on .state
  ├─ client                    AsyncClient(ASGITransport(wired_app))
  ├─ client_capturing_errors   same, raise_app_exceptions=False → 500s instead of raising
  └─ create_user               POSTs /users/, asserts 201, returns the body
```

Two details that matter when reading the tests:

- `db_engine` is **function-scoped**, so every test gets an empty schema. That is why
  `test_list_users_empty` can assert `== []`. `StaticPool` keeps a single connection alive
  so the `:memory:` database survives between sessions taken from the factory.
- `client_capturing_errors` exists because `ASGITransport` re-raises unhandled endpoint
  exceptions into the test by default. Tests that want to observe a real 500 — a Redis ping
  failure, a non-`HTTPError` raised by the outbound handler — use this client instead. See
  `test_health.py:58` and `test_external_api.py:80`.

## `fakes.py`

`FakeRedis` implements only the surface the application actually uses (`get`, `set`,
`delete`, `exists`, `ping`, `aclose`) and mirrors the real client's
`decode_responses=True` behaviour by storing and returning `str`.

Beyond that it is an **assertion recorder**: `set_calls`, `deleted_keys`, `ping_count`,
`closed`, plus `seed()` / `raw()` / `ttl_of()` helpers and a `fail_ping` hook for failure
injection. So `assert fake_redis.ttl_of(key) == 300` checks the TTL the application
actually passed, and cache expiry is simulated via `time.monotonic` rather than slept
through.

`MockHTTP` wraps `httpx.MockTransport` with a swappable `handler` —
`respond_with(503, ...)`, `raise_error(httpx.ConnectError(...))` — and records every
request, so `mock_http.last_url` can assert the outbound URL used in
`app/routers/external.py`.

## What each file covers

| File | Layer | Approach |
|---|---|---|
| `test_users_api.py` (470L) | CRUD + cache endpoint | Full ASGI round trips; asserts status/body **and** cache side effects |
| `test_user_service.py` | cache-aside orchestration | Direct `UserService` calls; monkeypatches `repository.get_by_id` to *explode*, proving a cache hit never hits the DB (`:108`) |
| `test_user_repository.py` | SQL | Real in-memory session — the SQL genuinely executes; asserts `create`/`update` **don't** commit (that's the service's job) |
| `test_dependencies.py` | DI | No ASGI at all — a `SimpleNamespace` stands in for `Request` (`:29`), then drives the generators with `anext`/`athrow` |
| `test_lifespan.py` | startup/shutdown | Runs the *real* lifespan against a bare `FastAPI()`; monkeypatches `create_redis_client` to a `FakeRedis` to assert `finally`-block cleanup even when the body raises. Ends with a `TestClient` smoke test that boots the actual app |
| `test_infrastructure.py` | config + factories | Asserts `Settings.model_fields` **declared defaults** rather than live values, since the test environment overrides several of them (`:37`) |
| `test_middleware_and_app.py` | request-ID middleware, OpenAPI | Checks that the middleware's `await request.body()` doesn't consume the body the endpoint needs (`:59`) |
| `test_logging.py` | loguru bridge, query timing | A `captured_records` fixture adds a list sink to loguru; also asserts the `query_start_time` stack doesn't grow |
| `test_schemas.py` | Pydantic | Pure unit tests; `:105` pins the cached-JSON shape the service hand-builds against what `get_user` reads back |
| `test_health.py` | liveness/readiness | Uses `dependency_overrides[get_db]` for the one case a state double can't express: a DB that raises |

## The one xfail

`test_logging.py:76` is a `strict=True` xfail documenting a real bug:
`InterceptHandler.emit`'s `ValueError` fallback sets `level = str(record.levelno)` and
passes it to `logger.log()`, but loguru only accepts registered level *names*, so it raises
again. Because the marker is strict, the suite will **fail** if someone fixes the bug
without deleting the xfail — it is a to-do with an alarm on it.

## Running

```powershell
cd c:\Rafi\projects\project_003\fastapi_app
.\.venv\Scripts\python.exe -m pytest -q                           # all
.\.venv\Scripts\python.exe -m pytest tests/test_users_api.py -v    # one file
.\.venv\Scripts\python.exe -m pytest -k cache                      # by name
```

`uv run pytest` works too. The two warnings the run emits are upstream deprecations
(starlette's `TestClient` wanting `httpx2`, an `anyio` alias) — not from this code.
