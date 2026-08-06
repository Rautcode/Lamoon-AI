"""System/health endpoints. /health needs no DB so it's a clean liveness probe."""
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": get_settings().app_name}


@router.get("/health/db")
def health_db() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"db": "ok"}
    except Exception as e:  # noqa: BLE001 — surface any connectivity issue as unhealthy
        return {"db": "down", "error": str(e)}
