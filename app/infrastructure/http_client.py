import httpx

from app.config import settings


def create_http_client() -> httpx.AsyncClient:

    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.http_timeout,
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
        follow_redirects=True,
    )
