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
    # Where a successful/failed OAuth callback sends the browser (this app has
    # no server-rendered login page, so the callback can't render one itself).
    # Success: tokens in the URL FRAGMENT (#...), which never leaves the
    # browser — not query params, not logged by any server/proxy in between.
    oauth_frontend_redirect: str = "http://localhost:3000/oauth/callback"

    # AI
    gemini_api_key: str = ""
    ai_default_model: str = "gemini-2.5-flash"

    # Storage. Backends are chosen PER PURPOSE (ADR-0006 as amended): payroll
    # and compliance artifacts are statutory records with a retention
    # obligation and go to S3-compatible object storage; ATS documents are
    # collaborative and stay on Drive. Both default to local so a fresh
    # checkout and CI need no configuration.
    storage_dir: str = "./_storage"
    storage_backend_payroll: str = "local"  # local | s3
    storage_backend_ats: str = "local"  # local | drive
    s3_bucket: str = ""
    #: Empty for AWS; set for MinIO/R2/Wasabi. This is what makes it
    #: "S3-compatible" rather than "S3".
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_prefix: str = ""

    # Email. Empty smtp_host → log-only (dev). Set these to actually send.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@lamoon.local"

    # This API's own externally-reachable base URL. Used for the candidate
    # booking link (ponytail: points at the JSON endpoint directly until
    # apps/web has a real page) and to build the OAuth redirect_uri, which
    # must exactly match what's registered with each provider's app.
    api_base_url: str = "http://localhost:8000"

    # Comma-separated browser origins allowed to call this API (apps/web's dev
    # server by default). No wildcard: credentials/auth headers require an
    # explicit origin list per the CORS spec anyway.
    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
