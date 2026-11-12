"""FastAPI application factory."""

import re
import time
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
from app.api.routers.assistant import router as assistant_router
from app.api.routers.recommendations import router as recommendations_router
from app.config import Settings
from app.infrastructure.auth.jwt_verifier import JwtVerifier, jwks_key_resolver
from app.infrastructure.db.session import create_engine
from app.logging import configure_logging
from app.startup_checks import (
    ExecutionPrerequisites,
    assert_rls_enforced,
    validate_auth_readiness,
    validate_embedding_config,
    validate_execution_safety,
    validate_llm_config,
)

log = structlog.get_logger()


def _access_log(request: Request, status: int, duration_ms: float) -> None:
    """One line per request. Deliberately omits the query string, headers and bodies: they can
    carry tokens and personal data. The route template (``/recommendations/{rec_id}``) is logged
    instead of the raw path so identifiers do not end up in logs."""
    route = request.scope.get("route")
    log.info(
        "request",
        method=request.method,
        route=getattr(route, "path", "unmatched"),
        status=status,
        duration_ms=round(duration_ms, 1),
        request_id=getattr(request.state, "request_id", None),
        org_id=getattr(request.state, "org_id", None),
        role=getattr(request.state, "role", None),
    )


# A caller-supplied correlation id is bound into every log line and echoed back, so only a short,
# plain token is accepted; anything else (newlines, spaces, long strings) is replaced.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def create_app(
    settings: Settings | None = None, prereqs: ExecutionPrerequisites | None = None
) -> FastAPI:
    settings = settings or Settings()
    # Fail fast, before anything else starts (ADR 0007).
    validate_execution_safety(settings, prereqs or ExecutionPrerequisites())
    validate_auth_readiness(settings)
    validate_llm_config(settings)
    validate_embedding_config(settings)
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if settings.database_url is not None:
            engine = create_engine(settings.database_url.get_secret_value())
            if settings.app_env == "production":  # fail before serving, not on the first request
                await assert_rls_enforced(engine, settings.db_app_role)
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

    production = settings.app_env == "production"
    app = FastAPI(
        title="AdPilot AI",
        version="0.1.0",
        lifespan=lifespan,
        # The API description is not published from a production deployment.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    # With auth configured, bearer JWTs are required and the dev header is ignored (api/tenancy.py).
    app.state.token_verifier = (
        JwtVerifier(
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
            key_resolver=jwks_key_resolver(settings.auth_jwks_url),
            algorithms=settings.auth_jwt_algorithms,
        )
        if settings.auth_configured
        else None
    )
    app.state.membership_directory = None  # defaults to the SQL directory; tests inject one
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Org-Id", "X-Dev-Org-Id"],
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _access_log(request, status, (time.perf_counter() - started) * 1000)

    register_error_handlers(app)
    app.include_router(analytics_router)
    app.include_router(assistant_router)
    app.include_router(recommendations_router)

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
