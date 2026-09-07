from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infrastructure.database import (
    create_db_engine,
    create_session_factory,
)

from app.infrastructure.redis import (
    create_redis_client,
)

from app.infrastructure.http_client import (
    create_http_client,
)

from app.models.user import Base

from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI):

    # ==================================================
    # STARTUP
    # ==================================================

    logger.info("========================================")
    logger.info("Application startup")
    logger.info("========================================")

    # --------------------------------------------------
    # 1. DATABASE
    # --------------------------------------------------

    db_engine = create_db_engine()

    db_session_factory = create_session_factory(db_engine)

    app.state.db_engine = db_engine
    app.state.db_session_factory = db_session_factory

    logger.info("✓ Database initialized")

    # --------------------------------------------------
    # 1.1. CREATE DATABASE TABLES
    # --------------------------------------------------

    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # --------------------------------------------------
    # 2. REDIS
    # --------------------------------------------------

    redis_client = create_redis_client()

    app.state.redis = redis_client

    logger.info("✓ Redis initialized")

    # --------------------------------------------------
    # 3. HTTP CLIENT
    # --------------------------------------------------

    http_client = create_http_client()

    app.state.http_client = http_client

    logger.info("✓ HTTP client initialized")

    # ==================================================
    # APPLICATION IS RUNNING
    # ==================================================

    try:
        yield

    finally:
        # ==================================================
        # SHUTDOWN
        # ==================================================

        logger.info("========================================")
        logger.info("Application shutdown")
        logger.info("========================================")

        # --------------------------------------------------
        # 1. HTTP CLIENT
        # --------------------------------------------------

        await http_client.aclose()

        logger.info("✓ HTTP client closed")

        # --------------------------------------------------
        # 2. REDIS
        # --------------------------------------------------

        await redis_client.aclose()

        logger.info("✓ Redis closed")

        # --------------------------------------------------
        # 3. DATABASE
        # --------------------------------------------------

        await db_engine.dispose()

        logger.info("✓ Database connection pool disposed")

        logger.info("========================================")
        logger.info("Shutdown completed")
        logger.info("========================================")
