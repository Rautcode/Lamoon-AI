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
    oauth_state_ttl_min: int = 10

    # OAuth (Google/Microsoft). Empty client_id → that provider's /start 503s.
    # Endpoints (authorize/token/userinfo URLs) are public and hardcoded in
    # core/auth/oauth.py; only these credentials are secret/configurable.
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    # Must exactly match the redirect URI registered with each provider.
    oauth_redirect_base: str = "http://localhost:8000"

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

    # Used to build the candidate-facing booking link. ponytail: points at the
    # API's own JSON endpoint (usable today) until apps/web has a real page.
    api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
