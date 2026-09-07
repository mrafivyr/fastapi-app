import uuid
import time

from fastapi import FastAPI, Request
from scalar_fastapi import get_scalar_api_reference
from loguru import logger

from app.lifespan import lifespan

from app.routers.health import router as health_router
from app.routers.users import router as users_router
from app.routers.external import router as external_router

from app.logging_config import setup_logging
from app.db_logging import register_db_logging
setup_logging()          # before anything else logs
register_db_logging()    # before any DB engine is created

app = FastAPI(
    title="FastAPI Application",
    description="Production-style FastAPI example",
    version="1.0.0",
    # Application startup/shutdown
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()

    with logger.contextualize(request_id=request_id):
        body = await request.body()

        logger.info(
            "Request started: {} {} body={}",
            request.method,
            request.url.path,
            body,
        )

        response = await call_next(request)

        duration = (time.perf_counter() - start_time) * 1000

        logger.info(
            "Request completed {} {} → {} {:.2f}ms",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )

    response.headers["X-Request-ID"] = request_id
    return response


# ======================================================
# ROUTERS
# ======================================================

app.include_router(health_router)

app.include_router(users_router)

app.include_router(external_router)


# ======================================================
# SCALAR API DOCUMENTATION
# ======================================================


@app.get("/scalar", include_in_schema=False)
async def scalar_docs():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )
