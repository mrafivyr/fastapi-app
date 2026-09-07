import json

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse, UserUpdate

from loguru import logger


class UserService:
    def __init__(
        self,
        db: AsyncSession,
        redis: Redis,
    ):
        self.repository = UserRepository(db)
        self.redis = redis
        self.db = db

    # ==================================================
    # CREATE USER
    # ==================================================

    async def create_user(
        self,
        data: UserCreate,
    ):

        user = await self.repository.create(data)

        await self.db.commit()

        await self.db.refresh(user)

        # Build cache immediately after creation
        cache_key = f"user:{user.id}"

        cache_value = {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        }

        await self.redis.set(
            cache_key,
            json.dumps(cache_value),
            ex=300,  # 5 minutes
        )

        return user

    # ==================================================
    # UPDATE USER
    # ==================================================
    async def update_user(
        self,
        user_id: int,
        data: UserUpdate,
    ):
        user = await self.repository.update(
            user_id,
            data,
        )

        if user is None:
            return None

        await self.db.commit()

        await self.db.refresh(user)

        # Invalidate old cached value
        await self.redis.delete(f"user:{user_id}")

        return user

    # ==================================================
    # GET USER WITH CACHE
    # ==================================================

    async def get_user(
        self,
        user_id: int,
    ):

        cache_key = f"user:{user_id}"

        # ------------------------------------------------
        # 1. Check Redis
        # ------------------------------------------------

        cached_user = await self.redis.get(cache_key)

        if cached_user is not None:
            logger.info(f"Cache HIT: {cache_key}")

            return UserResponse.model_validate_json(cached_user)

        # ------------------------------------------------
        # 2. Cache MISS -> Database
        # ------------------------------------------------

        logger.info(f"Cache MISS: {cache_key}")

        user = await self.repository.get_by_id(user_id)

        if user is None:
            return None

        user_response = UserResponse.model_validate(user)

        # ------------------------------------------------
        # 3. Build cache
        # ------------------------------------------------
        await self.redis.set(
            cache_key,
            # json.dumps(cache_value),
            user_response.model_dump_json(),
            ex=300,
        )

        # ------------------------------------------------
        # 4. Return DB result
        # ------------------------------------------------
        return user_response

    # ==================================================
    # GET ALL USERS
    # ==================================================

    async def get_users(
        self,
    ):

        return await self.repository.get_all()
