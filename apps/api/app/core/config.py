"""Settings from environment. One source of truth for config."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Lamoon HR"
    environment: str = "dev"

    # Infra. `app` is a deliberately non-superuser role — see db/init/01-app-role.sql.
    # Never point this at the `lamoon` bootstrap superuser: superusers implicitly
    # BYPASSRLS, which silently defeats every tenant boundary (ADR-0002).
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/lamoon"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    # ponytail: overridden by env in every real deploy; ≥32 bytes to satisfy HS256.
    jwt_secret: str = "dev-only-insecure-secret-change-me-in-production-0123456789"
    jwt_alg: str = "HS256"
    access_ttl_min: int = 30
    refresh_ttl_days: int = 14

    # AI
    gemini_api_key: str = ""
    ai_default_model: str = "gemini-2.5-flash"

    # Storage (dev). ponytail: local FS now; DriveBlobStore/S3 behind BlobStore later.
    storage_dir: str = "./_storage"

    # Email. Empty smtp_host → log-only (dev). Set these to actually send.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@lamoon.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
