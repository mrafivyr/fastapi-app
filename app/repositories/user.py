from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate

from loguru import logger


class UserRepository:
    def __init__(
        self,
        db: AsyncSession,
    ):
        self.db = db

    # ==================================================
    # CREATE
    # ==================================================

    async def create(
        self,
        data: UserCreate,
    ) -> User:

        user_existing = await self.get_by_email(data.email)

        if user_existing is not None:
            logger.warning(f"User with email {data.email} already exists")
            raise ValueError("User with this email already exists")

        user = User(
            name=data.name,
            email=data.email,
        )

        self.db.add(user)

        await self.db.flush()

        return user

    # ==================================================
    # UPDATE
    # ==================================================
    async def update(
        self,
        user_id: int,
        data: UserUpdate,
    ):

        user = await self.get_by_id(user_id)

        if user is None:
            return None

        user_existing = await self.get_by_email(data.email)

        if user_existing is not None and user_existing.id != user_id:
            raise ValueError("User with this email already exists")

        update_data = data.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            setattr(user, field, value)

        await self.db.flush()

        return user

    # ==================================================
    # GET BY ID
    # ==================================================

    async def get_by_id(
        self,
        user_id: int,
    ) -> User | None:

        result = await self.db.execute(select(User).where(User.id == user_id))

        return result.scalar_one_or_none()

    # ==================================================
    # GET BY ID
    # ==================================================

    async def get_by_email(
        self,
        email: str | None,
    ) -> User | None:

        result = await self.db.execute(select(User).where(User.email == email))

        return result.scalar_one_or_none()

    # ==================================================
    # GET ALL
    # ==================================================

    async def get_all(
        self,
    ) -> list[User]:

        result = await self.db.execute(select(User))

        return list(result.scalars().all())
