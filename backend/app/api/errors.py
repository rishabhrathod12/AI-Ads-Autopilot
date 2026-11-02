"""RFC 7807 problem-details error handling."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.application.analytics.dashboard import NoDataError
from app.application.forecasting.service import InsufficientHistoryError, NoTrafficError
from app.domain.kpi.periods import NoMatureDataError

PROBLEM_JSON = "application/problem+json"
log = structlog.get_logger()


def problem(request: Request, status: int, title: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "request_id": getattr(request.state, "request_id", None),
    }
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(request, 422, "Validation error", f"{len(exc.errors())} invalid field(s)")

    @app.exception_handler(NoDataError)
    async def _no_data(request: Request, exc: NoDataError) -> JSONResponse:
        return problem(request, 404, "No advertising data", str(exc))

    @app.exception_handler(NoMatureDataError)
    async def _no_mature(request: Request, exc: NoMatureDataError) -> JSONResponse:
        return problem(
            request, 422, "No mature data in period",
            "Every day in this period is still inside the attribution window.",
        )  # fmt: skip

    @app.exception_handler(InsufficientHistoryError)
    async def _short_history(request: Request, exc: InsufficientHistoryError) -> JSONResponse:
        return problem(request, 422, "Not enough history to forecast", str(exc))

    @app.exception_handler(NoTrafficError)
    async def _no_traffic(request: Request, exc: NoTrafficError) -> JSONResponse:
        return problem(request, 422, "No traffic to forecast", str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals to the client; details go to the log only.
        log.error("unhandled_exception", error_type=type(exc).__name__)
        return problem(request, 500, "Internal server error")
