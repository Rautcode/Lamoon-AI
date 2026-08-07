"""App factory + correlation middleware. Modules register their routers here —
the one place the monolith is wired. Boots without a DB (health is static).
"""
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core import context
from app.core.config import get_settings
from app.modules.ats.routes import router as ats_router
from app.modules.auth.routes import router as auth_router
from app.modules.hr_core.routes import router as hr_core_router
from app.modules.leave.routes import router as leave_router
from app.modules.public.routes import router as public_router
from app.modules.system.routes import router as system_router


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, version="0.1.0")

    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # request_id scopes this call; correlation_id stitches an async flow (platform §10).
        context.request_id.set(rid)
        context.correlation_id.set(request.headers.get("X-Correlation-ID") or rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    app.include_router(system_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(hr_core_router, prefix="/api/v1")
    app.include_router(leave_router, prefix="/api/v1")
    app.include_router(ats_router, prefix="/api/v1")
    app.include_router(public_router, prefix="/api/v1")
    # ponytail: further feature routers register here as each module ships (auth, hr_core, ...).
    return app


app = create_app()
