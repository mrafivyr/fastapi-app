from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request

from redis.asyncio import Redis

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.dependencies import get_redis


router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


@router.get("")
async def health():

    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):

    # Check DB
    await db.execute(text("SELECT 1"))

    # Check Redis
    await redis.ping()

    return {
        "status": "ready",
        "database": "ok",
        "redis": "ok",
    }
