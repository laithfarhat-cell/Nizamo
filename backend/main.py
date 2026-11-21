"""Nizamo ERP FastAPI Application Entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.database import Base, engine
from backend.core.registry import registry
from backend.core.router import router as core_router


# Ensure all core models and discovered ERP plugin modules are loaded in topological order
import backend.core.tenancy  # noqa: F401
import backend.core.sequences  # noqa: F401
import backend.core.security  # noqa: F401
import backend.core.chatter  # noqa: F401

registry.load_all()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # Ensure database tables exist on startup (production also runs `alembic upgrade head`)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Modular Odoo-Inspired Enterprise Resource Planning & Double-Entry Accounting Platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["Health"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "modules_loaded": registry.load_order,
        }

    app.include_router(core_router, prefix="/api/v1")
    registry.mount_routers(app, prefix="/api/v1")
    return app


app = create_app()
