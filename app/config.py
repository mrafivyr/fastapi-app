from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # -------------------------
    # Database
    # -------------------------

    database_url: str

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # -------------------------
    # Redis
    # -------------------------

    redis_url: str = "redis://localhost:6379/0"

    # -------------------------
    # HTTP Client
    # -------------------------

    http_timeout: float = 10.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    log_level: str = "INFO" 
    log_diagnose: bool = False  # Enable detailed exception information in logs (useful for debugging)
    db_echo: bool = True  # Enable SQLAlchemy echo for debugging (set to False in production)
    slow_query_threshold_ms: int = 100


settings = Settings()
