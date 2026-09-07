# FastAPI Application

A production-style async FastAPI service demonstrating layered architecture, cache-aside
Redis usage, pooled SQLAlchemy 2.0 access, structured request-scoped logging, and a test
suite that runs with no external services.

- **Stack** — Python 3.12, FastAPI, SQLAlchemy 2.0 (async), asyncpg / aiosqlite, Redis,
  httpx, loguru, Pydantic v2 + pydantic-settings, uv for dependency management
- **Tests** — 140 passed, 1 xfailed, ~1.5s, no Postgres/Redis/network required
- **Docs** — Swagger at `/docs`, Scalar at `/scalar`, schema at `/openapi.json`

---

## Table of contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Request lifecycle](#request-lifecycle)
- [The layers in detail](#the-layers-in-detail)
- [Caching model](#caching-model)
- [Transaction boundaries](#transaction-boundaries)
- [Logging](#logging)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Testing](#testing)
- [Docker](#docker)
- [Docker Compose](#docker-compose)
- [Kubernetes](#kubernetes)
- [Operational notes and known issues](#operational-notes-and-known-issues)

---

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                                            # create .venv from uv.lock
# write a .env — copy the block in Configuration below
uv run uvicorn app.main:app --reload
```

`DATABASE_URL` is the only required setting. For a zero-dependency spin-up:

```bash
DATABASE_URL=sqlite+aiosqlite:///./mydb.db uv run uvicorn app.main:app --reload
```

Redis is only contacted lazily, so `/health` and the docs work without it; `/users/*` and
`/health/ready` do not.

Then open <http://localhost:8000/scalar>.

---

## Architecture

```
                    HTTP
                     │
        ┌────────────▼─────────────┐
        │  request_id_middleware   │  X-Request-ID in/out, loguru.contextualize,
        │      (app/main.py)       │  request + response timing log
        └────────────┬─────────────┘
                     │
        ┌────────────▼─────────────┐
        │        Routers           │  HTTP concerns only: status codes,
        │     (app/routers/)       │  HTTPException, response_model
        └────────────┬─────────────┘
                     │ Depends(get_user_service)
        ┌────────────▼─────────────┐
        │        Service           │  orchestration: commit, cache-aside,
        │  (app/services/user.py)  │  ValueError as the domain error
        └────────────┬─────────────┘
                     │
        ┌────────────▼─────────────┐
        │       Repository         │  SQL only: select/add/flush.
        │(app/repositories/user.py)│  Never commits.
        └────────────┬─────────────┘
                     │
        ┌────────────▼─────────────┐
        │      Models / Base       │  SQLAlchemy DeclarativeBase
        │    (app/models/user.py)  │
        └──────────────────────────┘

Cross-cutting:
  app/config.py           Settings (pydantic-settings), instantiated at import time
  app/lifespan.py         builds + tears down engine, session factory, Redis, http client
  app/dependencies.py     reads those handles off request.app.state
  app/infrastructure/     the factory functions the lifespan calls
  app/logging_config.py   loguru sinks + stdlib→loguru interception
  app/db_logging.py       SQLAlchemy cursor-execute timing
```

### The `app.state` pattern

The lifespan constructs every long-lived resource once and parks it on `app.state`:

| `app.state` attribute | Built by | Used by |
|---|---|---|
| `db_engine` | `create_db_engine()` | disposed at shutdown |
| `db_session_factory` | `create_session_factory(engine)` | `get_db()` |
| `redis` | `create_redis_client()` | `get_redis()` |
| `http_client` | `create_http_client()` | `get_http_client()` |

Dependencies then read from the incoming `Request`:

```python
def get_redis(request: Request) -> Redis:
    return request.app.state.redis
```

This is what makes the app cheap to test: replacing three attributes on `app.state`
substitutes all infrastructure while every real dependency function still executes. See
[Testing](#testing).

### File map

| Path | Lines | Responsibility |
|---|---:|---|
| `app/main.py` | 80 | app construction, request-ID middleware, router registration, `/scalar` |
| `app/config.py` | 39 | `Settings`; module-level `settings` built at import |
| `app/lifespan.py` | 116 | startup/shutdown; `create_all`; ordered teardown in `finally` |
| `app/dependencies.py` | 69 | `get_db`, `get_redis`, `get_http_client`, `get_user_service` |
| `app/infrastructure/database.py` | 42 | engine + session factory construction |
| `app/infrastructure/redis.py` | 12 | `redis.from_url(decode_responses=True)` |
| `app/infrastructure/http_client.py` | 17 | pooled `httpx.AsyncClient` |
| `app/models/user.py` | 37 | `Base`, `User` |
| `app/schemas/user.py` | 30 | `UserCreate`, `UserUpdate`, `UserResponse` |
| `app/repositories/user.py` | 107 | CRUD SQL |
| `app/services/user.py` | 138 | commit + cache orchestration |
| `app/routers/users.py` | 139 | `/users` CRUD + cache inspection |
| `app/routers/health.py` | 42 | `/health`, `/health/ready` |
| `app/routers/external.py` | 32 | `/external/users` upstream proxy |
| `app/logging_config.py` | 86 | loguru configuration, `InterceptHandler` |
| `app/db_logging.py` | 52 | per-query duration logging |

---

## Request lifecycle

Taking `GET /users/1` as the example:

1. **Middleware** (`app/main.py:28`) — takes the caller's `X-Request-ID` or mints a
   `uuid4()`, starts a `perf_counter`, enters `logger.contextualize(request_id=...)` so
   every log line in this request carries the id, reads the body, logs
   `Request started`.
2. **Routing** — FastAPI matches `users_router` (`prefix="/users"`), coerces `user_id` to
   `int` (a non-integer yields 422 before any code runs).
3. **Dependency resolution** — `get_user_service` → `get_db` + `get_redis`. `get_db`
   opens an `AsyncSession` from the factory on `app.state` and yields it inside
   `async with`.
4. **Service** — `UserService.get_user(1)` does the cache-aside read (see below).
5. **Response model** — `response_model=UserResponse` filters the returned object down to
   `{id, name, email, created_at}`.
6. **Teardown** — `get_db`'s generator resumes: on success the `async with` closes the
   session; on an exception it calls `session.rollback()` and re-raises.
7. **Middleware exit** — logs `Request completed ... → 200 12.34ms`, sets the
   `X-Request-ID` response header (present on 4xx/5xx too).

---

## The layers in detail

### Model

```python
class User(Base):
    __tablename__ = "users"
    id:         Mapped[int]      = mapped_column(primary_key=True, autoincrement=True)
    name:       Mapped[str]      = mapped_column(String(100), nullable=False)
    email:      Mapped[str]      = mapped_column(String(255), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(timezone.utc),
                                                 nullable=False)
```

`created_at` is generated in Python, not by the database, so it reflects application
clock time.

### Schemas

| Schema | Shape | Notes |
|---|---|---|
| `UserCreate` | `name: str`, `email: EmailStr` | no `id` field, so a client-supplied `id` is silently dropped |
| `UserUpdate` | both optional, default `None` | partial update via `model_dump(exclude_unset=True)` |
| `UserResponse` | `id`, `name`, `email`, `created_at` | `from_attributes=True` for ORM reads |

`EmailStr` requires `email-validator`, which arrives via `fastapi[standard]`.

### Repository — SQL, and nothing else

`create()` does a `get_by_email()` pre-check, raises `ValueError` on a hit, then
`add()` + `flush()`. `flush()` (not `commit()`) means the row is queryable and `id` /
`created_at` are populated while the transaction stays open for the service to finish.

`update()` fetches by id (returns `None` if absent), pre-checks the target email against
*other* rows (`user_existing.id != user_id`, so re-assigning your own email is allowed),
applies only the fields the client actually sent, and flushes.

### Service — orchestration

`UserService` owns three things the repository deliberately doesn't: `commit()`, cache
maintenance, and the domain error contract (`ValueError` for a duplicate email, `None`
for not-found). Routers translate those into 409 and 404.

### Routers

Thin by design:

```python
try:
    user = await service.create_user(data)
except ValueError as e:
    raise HTTPException(status_code=409, detail=str(e))
return user
```

---

## Caching model

Cache-aside on a 300-second TTL, keyed `user:{id}`.

```
CREATE  ──> INSERT + commit ──> SET user:{id} (ex=300)        write-through warm
READ    ──> GET user:{id} ──hit──> return, DB never touched
                        └─miss──> SELECT ──> SET user:{id} (ex=300) ──> return
UPDATE  ──> UPDATE + commit ──> DEL user:{id}                invalidate, no re-warm
LIST    ──> SELECT *                                          cache bypassed entirely
```

Details that matter:

- **Misses are not cached.** A 404 leaves Redis untouched, so a hot missing id hits the
  DB every time.
- **Two writers, one reader.** `create_user` hand-builds the JSON
  (`json.dumps` with `created_at.isoformat()`); `get_user` writes
  `user_response.model_dump_json()`. Both are read back with
  `UserResponse.model_validate_json`. `tests/test_schemas.py:105` pins that
  compatibility — change one side and that test fails.
- **`GET /users/` bypasses the cache**, so a stale entry never affects a list response.
- **Redis stores strings**, not bytes — `create_redis_client()` sets
  `decode_responses=True`.
- **Cache failures are not caught.** A Redis outage surfaces as a 500 from `/users/*`;
  there is no degrade-to-database path.

---

## Transaction boundaries

```
get_db (dependency)      opens session, rollback + re-raise on exception, closes
  └─ service             commit(), refresh()
       └─ repository     add / flush / select      ← never commits
```

Three consequences:

- `expire_on_commit=False` on the session factory means committed ORM objects stay
  readable, which is why the service can return `user` straight to the response model.
- `autoflush=False` means writes hit the DB only at the explicit `flush()`.
- `get_db` does **not** commit for you (asserted by `tests/test_dependencies.py:77`).
  A handler that forgets `service.commit()` silently discards its writes.

---

## Logging

`setup_logging()` and `register_db_logging()` run at import of `app/main.py`, before the
app object exists.

**Sinks** — two, both `enqueue=True` (thread-safe, non-blocking):

| Sink | Format |
|---|---|
| `sys.stderr` | colorized, `time \| level \| name:function:line \| request_id \| message` |
| `app.log` | same fields, uncolored |

**Request correlation** — `logger.configure(extra={"request_id": "-"})` provides a
default so log lines outside a request scope don't raise on the `{extra[request_id]}`
placeholder. Inside a request the middleware's `contextualize()` replaces it.

**stdlib interception** — `InterceptHandler` is installed as the root handler at level 0
and explicitly on `uvicorn`, `uvicorn.error`, `uvicorn.access`, `sqlalchemy.engine`,
`sqlalchemy.engine.Engine`, `httpx`, `httpcore` with `propagate = False`. It walks back
out of the `logging` module so `{name}:{function}:{line}` points at the real caller.

**Query timing** — `db_logging.py` registers `before_cursor_execute` /
`after_cursor_execute` on the `Engine` *class*, so every engine is covered. Start times
go on a per-connection stack (`conn.info["query_start_time"]`), which nests correctly and
is popped on completion.

**`diagnose=settings.log_diagnose`**, default `False`, deliberately: loguru's `diagnose`
prints local variable values in tracebacks, which would leak `settings.database_url`
credentials into logs.

---

## Configuration

`Settings` (pydantic-settings) reads environment variables first, then `.env`, and
ignores unknown keys (`extra="ignore"`). It is instantiated **at import time**, so
variables must be set before anything under `app.` is imported.

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | str | **required** | e.g. `postgresql+asyncpg://user:pass@host:5432/db` or `sqlite+aiosqlite:///./mydb.db` |
| `DB_POOL_SIZE` | int | `10` | persistent pool connections |
| `DB_MAX_OVERFLOW` | int | `20` | burst connections above pool size |
| `DB_POOL_TIMEOUT` | int | `30` | seconds to wait for a connection |
| `DB_POOL_RECYCLE` | int | `1800` | recycle connections older than this |
| `DB_ECHO` | bool | `True` | ⚠️ dev-oriented default; logs SQL. Set `false` in production |
| `REDIS_URL` | str | `redis://localhost:6379/0` | |
| `HTTP_TIMEOUT` | float | `10.0` | outbound httpx timeout (all phases) |
| `LOG_LEVEL` | str | `INFO` | applied to both sinks |
| `LOG_DIAGNOSE` | bool | `False` | ⚠️ leaks locals (incl. credentials) into tracebacks if enabled |
| `SLOW_QUERY_THRESHOLD_MS` | int | `100` | **currently unused** — see [known issues](#operational-notes-and-known-issues) |

`.env` is gitignored and excluded by `.dockerignore`. Example:

```dotenv
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app
REDIS_URL=redis://localhost:6379/0
HTTP_TIMEOUT=10
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_ECHO=false
LOG_LEVEL=INFO
```

Note: `pool_size`/`max_overflow`/`pool_timeout` are rejected by SQLite's **memory**
dialect (it uses `StaticPool`). A file-backed SQLite URL works fine.

---

## API reference

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/health` | 200 `{"status":"ok"}` | — (touches no infrastructure) |
| `GET` | `/health/ready` | 200 `{"status":"ready","database":"ok","redis":"ok"}` | 500 if `SELECT 1` or `PING` fails |
| `POST` | `/users/` | 201 `UserResponse` | 409 duplicate email · 422 validation |
| `GET` | `/users/` | 200 `list[UserResponse]` | — |
| `GET` | `/users/{user_id}` | 200 `UserResponse` | 404 · 422 non-integer id |
| `PUT` | `/users/{user_id}` | 200 `UserResponse` | 404 · 409 email taken · 422 |
| `GET` | `/users/{user_id}/cache` | 200 `{"key":..., "value": <raw string or null>}` | — |
| `GET` | `/external/users` | 200 upstream JSON | 502 on any `httpx.HTTPError` |

Every response, including errors, carries `X-Request-ID`.

```bash
curl -X POST localhost:8000/users/ \
  -H 'Content-Type: application/json' \
  -d '{"name":"Alice","email":"alice@example.com"}'

curl localhost:8000/users/1
curl localhost:8000/users/1/cache      # inspect the cached string
curl -X PUT localhost:8000/users/1 -H 'Content-Type: application/json' -d '{"name":"Alice II"}'
```

`PUT` is a partial update despite the verb: `{"name": "X"}` leaves `email` alone, and
`{}` is a no-op returning the unchanged row.

`/external/users` proxies `https://jsonplaceholder.typicode.com/users` — a hardcoded URL
demonstrating the shared, pooled `httpx.AsyncClient`. Only `httpx.HTTPError` maps to 502;
anything else is a genuine 500.

---

## Testing

Full detail lives in [`tests.md`](tests.md). Summary:

```bash
uv run pytest                            # all: 140 passed, 1 xfailed, ~1.5s
uv run pytest tests/test_users_api.py -v
uv run pytest -k cache
```

`asyncio_mode = "auto"` in `pyproject.toml`, so `async def test_*` needs no decorator.

The suite requires **no** Postgres, Redis or network access. Two mechanisms do that
work, both in `tests/conftest.py`:

1. Environment variables are set **before** the `app.` imports, because
   `app.config.settings` is built at import time.
2. `wired_app` puts test doubles on `app.state` and skips the lifespan — so
   `get_db`/`get_redis`/`get_http_client` all execute for real against in-memory SQLite,
   `FakeRedis`, and `httpx.MockTransport`. Nothing is faked at the DI layer.

`tests/test_lifespan.py` additionally runs the *real* lifespan, and finishes with a
`TestClient` smoke test that boots the actual app.

---

## Docker

The [`Dockerfile`](Dockerfile) is multi-stage:

```
base ──> deps ─────────────────────> prod    runtime deps only, no uv, non-root uid 1001
           └──> deps-dev ──> test          + pytest, + tests/, suite runs during build
```

```bash
docker build --target test .                    # CI gate: build fails if tests fail
docker build --target prod -t fastapi-app .     # runtime image

docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://app:app@host.docker.internal:5432/app \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  fastapi-app
```

- `pyproject.toml`/`uv.lock` are copied before the source, so editing `app/` doesn't
  invalidate the dependency layer. `uv sync --locked` fails the build on a stale lock.
- The project is **virtual** (`uv.lock`: `source = { virtual = "." }`), so `app` is never
  pip-installed — hence `PYTHONPATH=/app`.
- The `test` stage needs no services: `conftest.py` supplies its own configuration.
- `prod` sets `DB_ECHO=false` because the application's own default is `True`.
- `/app` is chowned to the runtime user: `setup_logging()` writes to the *relative* path
  `app.log`, so the working directory must be writable. **This image cannot run with a
  read-only root filesystem** until that sink path is made configurable.
- `HEALTHCHECK` hits `/health` (pure liveness) via `urllib` — there is no `curl` in the
  slim image.
- The `CMD` is a single uvicorn process. The lifespan builds one connection pool **per
  process**, so scale with replicas rather than `--workers` unless you lower the pool
  sizes to match.

---

## Docker Compose

Save as `docker-compose.yml` in this directory:

```yaml
name: fastapi-app

services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 5s
      retries: 10
    # Comment out to keep Postgres off the host network.
    ports:
      - "5432:5432"

  redis:
    image: redis:8-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    ports:
      - "6379:6379"

  api:
    build:
      context: .
      target: prod
    environment:
      DATABASE_URL: postgresql+asyncpg://app:app@db:5432/app
      REDIS_URL: redis://redis:6379/0
      DB_ECHO: "false"
      LOG_LEVEL: INFO
      # 3 replicas x (pool_size 10 + overflow 20) = 90 < Postgres default 100.
      DB_POOL_SIZE: "10"
      DB_MAX_OVERFLOW: "20"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    # Let the lifespan close pools cleanly before SIGKILL.
    stop_grace_period: 30s

  # One-shot: docker compose run --rm test
  test:
    build:
      context: .
      target: test
    profiles: ["tools"]
    command: ["pytest", "-v"]

volumes:
  pgdata:
```

### Steps

```bash
# 1. Build and start the stack (Postgres and Redis come up healthy first)
docker compose up --build -d

# 2. Watch startup — the lifespan logs each resource as it initializes
docker compose logs -f api

# 3. Verify
curl localhost:8000/health              # {"status":"ok"}
curl localhost:8000/health/ready        # {"status":"ready","database":"ok","redis":"ok"}
curl -X POST localhost:8000/users/ -H 'Content-Type: application/json' \
     -d '{"name":"Alice","email":"alice@example.com"}'
curl localhost:8000/users/1/cache       # confirm the write-through warm

# 4. Open the docs
#    http://localhost:8000/scalar

# 5. Run the test suite in its own container (no services needed)
docker compose run --rm test

# 6. Scale the API (see the connection-budget note below)
docker compose up -d --scale api=3      # remove the `ports` mapping first

# 7. Tear down (add -v to drop the Postgres volume)
docker compose down
docker compose down -v
```

### Notes

- **No migration step.** The lifespan calls `Base.metadata.create_all`, so the `users`
  table appears on first boot. `create_all` only ever *creates* — it will not alter an
  existing table when the model changes. See [known issues](#operational-notes-and-known-issues).
- **Connection budget.** Each API container opens up to
  `DB_POOL_SIZE + DB_MAX_OVERFLOW` = 30 Postgres connections. Postgres defaults to
  `max_connections=100`, so 3 replicas is the practical ceiling before you either lower
  the pool sizes or raise `max_connections`.
- **Redis persistence is off** (`--save "" --appendonly no`). The cache is disposable by
  design; a cold Redis just means a run of cache misses.
- `depends_on: condition: service_healthy` matters here — the app has no connection
  retry loop, so an API container that starts before Postgres accepts connections will
  fail on its first request rather than reconnecting.
- **Don't add `env_file: .env`** unless you intend it: the image reads `.env` from its
  working directory, and `.dockerignore` deliberately keeps it out of the build.

---

## Kubernetes

Manifests for the application. Postgres and Redis are shown as minimal in-cluster
deployments for a dev cluster — **use a managed database and cache in production**, or a
maintained chart (`bitnami/postgresql`, `bitnami/redis`).

Save as `k8s/app.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: fastapi-app

---
# Credentials. In a real cluster source these from External Secrets / Vault / CSI
# rather than a committed manifest.
apiVersion: v1
kind: Secret
metadata:
  name: api-secrets
  namespace: fastapi-app
type: Opaque
stringData:
  DATABASE_URL: postgresql+asyncpg://app:app@postgres:5432/app
  REDIS_URL: redis://redis:6379/0

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: api-config
  namespace: fastapi-app
data:
  DB_ECHO: "false"
  LOG_LEVEL: "INFO"
  LOG_DIAGNOSE: "false"
  HTTP_TIMEOUT: "10"
  # 3 replicas x (5 + 10) = 45 connections. Keep this product under the
  # database's max_connections.
  DB_POOL_SIZE: "5"
  DB_MAX_OVERFLOW: "10"
  DB_POOL_TIMEOUT: "30"
  DB_POOL_RECYCLE: "1800"

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: fastapi-app
  labels:
    app: api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: api
    spec:
      # The lifespan closes the http client, Redis, and the DB pool in a `finally`.
      # Give it room before SIGKILL.
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        runAsGroup: 1001
        fsGroup: 1001
      containers:
        - name: api
          image: fastapi-app:latest      # replace with your registry path
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - configMapRef:
                name: api-config
            - secretRef:
                name: api-secrets
          # /health touches no infrastructure — a DB blip must not restart the pod.
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 3
          # /health/ready runs SELECT 1 + Redis PING and 500s if either fails,
          # which is exactly the signal to pull the pod out of the Service.
          readinessProbe:
            httpGet:
              path: /health/ready
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 3
            failureThreshold: 20
          resources:
            requests:
              cpu: 100m
              memory: 192Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            # Cannot be true: setup_logging() writes ./app.log into /app.
            # See known issues for the one-line app change that lifts this.
            readOnlyRootFilesystem: false

---
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: fastapi-app
spec:
  type: ClusterIP
  selector:
    app: api
  ports:
    - name: http
      port: 80
      targetPort: http

---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api
  namespace: fastapi-app
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: api

---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api
  namespace: fastapi-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 3
  # Every replica opens its own pool. maxReplicas x (DB_POOL_SIZE + DB_MAX_OVERFLOW)
  # must stay under the database's max_connections: 6 x 15 = 90.
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70

---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api
  namespace: fastapi-app
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
spec:
  ingressClassName: nginx
  rules:
    - host: api.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 80
```

Save as `k8s/datastores.yaml` (**dev clusters only**):

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: fastapi-app
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432

---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: fastapi-app
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:17-alpine
          env:
            - name: POSTGRES_USER
              value: app
            - name: POSTGRES_PASSWORD
              value: app
            - name: POSTGRES_DB
              value: app
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "app", "-d", "app"]
            initialDelaySeconds: 5
            periodSeconds: 5
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 5Gi

---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: fastapi-app
spec:
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: fastapi-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:8-alpine
          args: ["--save", "", "--appendonly", "no"]
          ports:
            - containerPort: 6379
          readinessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 3
            periodSeconds: 5
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 256Mi
```

### Steps

```bash
# 1. Build and publish the image (or load it into a local cluster)
docker build --target prod -t <registry>/fastapi-app:0.1.0 .
docker push <registry>/fastapi-app:0.1.0
#   kind:    kind load docker-image fastapi-app:latest
#   minikube: minikube image load fastapi-app:latest

# 2. Datastores first — the app has no connection retry loop
kubectl apply -f k8s/datastores.yaml
kubectl -n fastapi-app rollout status statefulset/postgres
kubectl -n fastapi-app wait --for=condition=available deployment/redis --timeout=120s

# 3. Application
kubectl apply -f k8s/app.yaml
kubectl -n fastapi-app rollout status deployment/api --timeout=180s

# 4. Verify — readiness gates the Service, so READY 3/3 already means
#    SELECT 1 and Redis PING both succeeded on every pod
kubectl -n fastapi-app get pods -o wide
kubectl -n fastapi-app logs -l app=api --tail=50 -f

# 5. Smoke test without an Ingress
kubectl -n fastapi-app port-forward svc/api 8000:80
curl localhost:8000/health/ready
curl -X POST localhost:8000/users/ -H 'Content-Type: application/json' \
     -d '{"name":"Alice","email":"alice@example.com"}'

# 6. Roll out a new version
kubectl -n fastapi-app set image deployment/api api=<registry>/fastapi-app:0.2.0
kubectl -n fastapi-app rollout status deployment/api
kubectl -n fastapi-app rollout undo deployment/api      # if it goes wrong

# 7. Inspect config and scaling
kubectl -n fastapi-app get hpa api
kubectl -n fastapi-app describe configmap api-config

# 8. Tear down
kubectl delete namespace fastapi-app
```

### Probe design

| Probe | Path | Why |
|---|---|---|
| `startupProbe` | `/health` | tolerates slow first boot (60s budget) without a long liveness delay |
| `livenessProbe` | `/health` | needs no infrastructure, so a Postgres blip can't restart every pod at once |
| `readinessProbe` | `/health/ready` | 500s when the DB or Redis is unreachable — the pod leaves the Service but keeps running |

Using `/health/ready` for liveness would be a mistake: a brief database outage would
restart the whole fleet, and restarts don't fix a database.

### Kubernetes-specific cautions

1. **Schema creation races.** All 3 replicas run `Base.metadata.create_all` concurrently
   at startup. It is `CREATE TABLE IF NOT EXISTS`-shaped, so this is usually benign, but
   concurrent DDL can deadlock. Move schema management to an Alembic migration run as a
   `Job` (or an `initContainer`) and drop the `create_all` call.
2. **Connection budget is the real scaling limit.** `maxReplicas × (DB_POOL_SIZE +
   DB_MAX_OVERFLOW)` must stay under the database's `max_connections`. The ConfigMap
   above lowers the pools to 5/10 precisely because HPA can multiply them by 6. Consider
   PgBouncer if you need to scale past that.
3. **`readOnlyRootFilesystem: false` is forced** by the `app.log` file sink. See below.
4. **Logs are written twice** — stderr (which your log collector reads) and `app.log`
   (which nothing reads, and which grows unbounded with no rotation).
5. **`terminationGracePeriodSeconds: 30`** gives the lifespan's `finally` block time to
   close the http client, Redis, and the engine pool. Too short and you leak server-side
   connections on every rollout.

---

## Operational notes and known issues

Found while documenting; listed with the fix so nothing is a surprise in production.

| # | Issue | Location | Impact / fix |
|---|---|---|---|
| 1 | `create_all` at startup instead of migrations | `app/lifespan.py:51` | never alters existing tables; races across replicas. Adopt Alembic, run as a Job |
| 2 | Every query logs at `WARNING` | `app/db_logging.py:48` | the slow-query threshold is commented out at `:42-47`, so `SLOW_QUERY_THRESHOLD_MS` does nothing and prod logs one WARNING per query. Restore the `if duration_ms >= threshold` guard |
| 3 | `app.log` uses a relative path | `app/logging_config.py:58` | forces a writable CWD, blocks `readOnlyRootFilesystem`, no rotation, duplicates stdout. Make the path a setting, add `rotation=`/`retention=`, or drop the sink in containers |
| 4 | `DB_ECHO` defaults to `True` | `app/config.py:35` | dev-oriented default; the Dockerfile and both deployment configs override it |
| 5 | Duplicate email is a `SELECT`-then-`INSERT` | `app/repositories/user.py:26` | two concurrent creates can both pass the check; the DB unique index then raises `IntegrityError`, which nothing catches → 500 instead of 409. Catch `IntegrityError` and re-raise as `ValueError` |
| 6 | No Redis degradation | `app/services/user.py:95` | a Redis outage 500s all of `/users/*` even though the DB is fine. Wrap cache reads in `try/except` and fall through |
| 7 | `UserUpdate(name=None)` is "set" | `tests/test_schemas.py:65` | an explicit `{"name": null}` is written to a `NOT NULL` column → 500. Validate, or exclude `None` values |
| 8 | `/users/{id}/cache` is public | `app/routers/users.py:124` | a debug endpoint in the published OpenAPI schema. Remove, or gate it behind auth and `include_in_schema=False` |
| 9 | `InterceptHandler` custom-level fallback is broken | `app/logging_config.py:16-17` | sets `level = str(record.levelno)` and passes it to `logger.log()`, which only accepts registered level *names*, so it raises again. Tracked by a `strict=True` xfail at `tests/test_logging.py:76` — deleting the marker is part of the fix |
| 10 | `requires-python = ">3.12"` | `pyproject.toml` | strictly greater — excludes 3.12.0 while allowing 3.12.13. Almost certainly `>=3.12` was meant |
| 11 | Phantom workspace member | `pyproject.toml` | `[tool.uv.workspace] members = ["python3.12"]` names a directory that doesn't exist. uv tolerates the empty glob silently; looks like a leftover |
| 12 | No authentication anywhere | all routers | every endpoint is unauthenticated. Add auth before exposing this through an Ingress |
| 13 | Hardcoded upstream URL | `app/routers/external.py:22` | `jsonplaceholder.typicode.com` should be a setting |

None of these break the test suite — it passes 140/1-xfail as written.
