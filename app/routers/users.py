from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from loguru import logger

from app.dependencies import get_user_service
from app.schemas.user import UserCreate, UserUpdate
from app.schemas.user import UserResponse
from app.services.user import UserService

from redis.asyncio import Redis

from app.dependencies import get_redis


router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


# ======================================================
# CREATE USER
# ======================================================


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    service: UserService = Depends(get_user_service),
):
    try:
        user = await service.create_user(data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return user


# ======================================================
# UPDATE USER
# ======================================================
@router.put(
    "/{user_id}",
    response_model=UserResponse,
)
async def update_user(
    user_id: int,
    data: UserUpdate,
    service: UserService = Depends(get_user_service),
):

    try:
        user = await service.update_user(
            user_id,
            data,
        )

    except ValueError as e:
        logger.warning(f"Error updating user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    return user


# ======================================================
# GET USER
# ======================================================


@router.get(
    "/{user_id}",
    response_model=UserResponse,
)
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service),
):

    user = await service.get_user(user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


# ======================================================
# GET USERS
# ======================================================


@router.get(
    "/",
    response_model=list[UserResponse],
)
async def get_users(
    service: UserService = Depends(get_user_service),
):

    return await service.get_users()


@router.get(
    "/{user_id}/cache",
)
async def get_cached_user(
    user_id: int,
    redis: Redis = Depends(get_redis),
):

    key = f"user:{user_id}"

    value = await redis.get(key)

    return {
        "key": key,
        "value": value,
    }
