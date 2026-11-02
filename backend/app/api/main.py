"""FastAPI application factory."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.errors import problem, register_error_handlers
from app.api.routers.analytics import router as analytics_router
from app.config import Settings
from app.infrastructure.db.session import create_engine
from app.logging import configure_logging
from app.startup_checks import (
    ExecutionPrerequisites,
    validate_auth_readiness,
    validate_execution_safety,
)

log = structlog.get_logger()


def create_app(
    settings: Settings | None = None, prereqs: ExecutionPrerequisites | None = None
) -> FastAPI:
    settings = settings or Settings()
    # Fail fast, before anything else starts (ADR 0007).
    validate_execution_safety(settings, prereqs or ExecutionPrerequisites())
    validate_auth_readiness(settings)
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if settings.database_url is not None:
            engine = create_engine(settings.database_url.get_secret_value())
        app.state.engine = engine
        log.info(
            "startup",
            env=settings.app_env,
            ads_provider=settings.ads_provider,
            execution_enabled=settings.ad_execution_enabled,
        )
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="AdPilot AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_error_handlers(app)
    app.include_router(analytics_router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "ads_provider": settings.ads_provider,
            "data_source": "MOCK" if settings.ads_provider == "mock" else "AMAZON_ADS_API",
            "execution_enabled": settings.ad_execution_enabled,
        }

    @app.get("/ready", response_model=None)
    async def ready(request: Request) -> dict[str, str] | Response:
        engine = request.app.state.engine
        if engine is None:
            return problem(request, 503, "Database not configured")
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("readiness_failed", error_type=type(exc).__name__)
            return problem(request, 503, "Database unavailable")
        return {"status": "ready"}

    return app
