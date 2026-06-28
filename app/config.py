from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Transaction Processing Pipeline"
    environment: str = "development"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/transactions"
    )
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    upload_dir: Path = Path("/data/uploads")
    max_upload_bytes: int = 10 * 1024 * 1024
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    llm_batch_size: int = 25
    llm_max_attempts: int = 3
    llm_request_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
