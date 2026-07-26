"""RFC 9457 Problem Details exception handling (Doc 05 section 5.6).

Registered as exception handlers rather than middleware so that FastAPI's own
validation errors are converted with the same shape as our domain errors.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from at_core.errors import AppError, ErrorCode, ValidationError

logger = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a deliberate AppError as a problem document."""
    assert isinstance(exc, AppError)
    problem = exc.to_problem(instance=request.url.path, trace_id=_trace_id(request))
    log = logger.bind(code=exc.code.value, status=exc.status, path=request.url.path)
    if exc.status >= 500:
        log.error("app_error", detail=exc.detail)
    else:
        log.info("app_error", detail=exc.detail)
    return JSONResponse(
        status_code=exc.status,
        content=problem,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=exc.headers or None,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert FastAPI/Pydantic validation failures into our problem shape."""
    assert isinstance(exc, RequestValidationError)
    errors = [
        {
            "field": ".".join(str(part) for part in err.get("loc", ())[1:]) or "body",
            "message": err.get("msg", "invalid"),
            "type": err.get("type", "value_error"),
        }
        for err in exc.errors()
    ]
    wrapped = ValidationError("One or more fields failed validation.", errors=errors)
    return JSONResponse(
        status_code=wrapped.status,
        content=wrapped.to_problem(instance=request.url.path, trace_id=_trace_id(request)),
        media_type=PROBLEM_CONTENT_TYPE,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert Starlette HTTPExceptions (404 routing, etc.) into problem documents."""
    assert isinstance(exc, StarletteHTTPException)
    code = ErrorCode.INTERNAL if exc.status_code >= 500 else ErrorCode.VALIDATION_FAILED
    if exc.status_code == 404:
        code = ErrorCode.ENGINE_NOT_FOUND
    elif exc.status_code == 401:
        code = ErrorCode.UNAUTHENTICATED
    elif exc.status_code == 403:
        code = ErrorCode.FORBIDDEN_ROLE

    problem = {
        "type": f"https://aerotwin.dev/errors/{code.value.lower().replace('_', '-')}",
        "title": str(exc.detail),
        "status": exc.status_code,
        "detail": str(exc.detail),
        "code": code.value,
        "instance": request.url.path,
    }
    if trace := _trace_id(request):
        problem["trace_id"] = trace
    return JSONResponse(
        status_code=exc.status_code, content=problem, media_type=PROBLEM_CONTENT_TYPE
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler. Never leaks internals to the client."""
    trace = _trace_id(request)
    logger.exception("unhandled_exception", path=request.url.path, trace_id=trace, error=str(exc))
    problem = {
        "type": "https://aerotwin.dev/errors/internal",
        "title": "Internal server error",
        "status": 500,
        "detail": "An unexpected error occurred. Quote the trace id when reporting this.",
        "code": ErrorCode.INTERNAL.value,
        "instance": request.url.path,
    }
    if trace:
        problem["trace_id"] = trace
    return JSONResponse(status_code=500, content=problem, media_type=PROBLEM_CONTENT_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all problem-details handlers to the application."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
