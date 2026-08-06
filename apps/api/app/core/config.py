"""Settings from environment. One source of truth for config."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Lamoon HR"
    environment: str = "dev"

    # Infra
    database_url: str = "postgresql+psycopg://lamoon:lamoon@localhost:5432/lamoon"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = "dev-only-change-me"  # ponytail: overridden by env in every real deploy
    jwt_alg: str = "HS256"
    access_ttl_min: int = 30
    refresh_ttl_days: int = 14

    # AI
    gemini_api_key: str = ""
    ai_default_model: str = "gemini-2.5-flash"

    # Storage (dev). ponytail: local FS now; DriveBlobStore/S3 behind BlobStore later.
    storage_dir: str = "./_storage"


@lru_cache
def get_settings() -> Settings:
    return Settings()
