from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def create_db_engine() -> AsyncEngine:

    return create_async_engine(
        settings.database_url,
        # -------------------------
        # Connection Pool
        # -------------------------
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        # Check connection before using it
        pool_pre_ping=True,
        echo=settings.db_echo,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:

    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        # Don't expire objects after commit
        expire_on_commit=False,
        # Usually preferable for web applications
        autoflush=False,
    )



