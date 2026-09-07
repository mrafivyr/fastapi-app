import httpx

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from app.dependencies import get_http_client


router = APIRouter(
    prefix="/external",
    tags=["External APIs"],
)


@router.get("/users")
async def get_external_users(
    client: httpx.AsyncClient = Depends(get_http_client),
):

    try:
        response = await client.get("https://jsonplaceholder.typicode.com/users")

        response.raise_for_status()

        return response.json()

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"External service error: {exc}",
        )
