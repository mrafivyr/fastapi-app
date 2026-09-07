from typing import AsyncGenerator

import httpx

from fastapi import Request, Depends

from redis.asyncio import Redis

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.user import UserService


# ======================================================
# DATABASE SESSION
# ======================================================


async def get_db(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:

    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        try:
            # Give session to endpoint/service
            yield session

        except Exception:
            # Something failed
            await session.rollback()

            raise


# ======================================================
# REDIS
# ======================================================


def get_redis(
    request: Request,
) -> Redis:

    return request.app.state.redis


# ======================================================
# HTTP CLIENT
# ======================================================


def get_http_client(
    request: Request,
) -> httpx.AsyncClient:

    return request.app.state.http_client


def get_user_service(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserService:

    return UserService(
        db=db,
        redis=redis,
    )
