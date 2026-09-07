import redis.asyncio as redis

from app.config import settings


def create_redis_client() -> redis.Redis:

    return redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
