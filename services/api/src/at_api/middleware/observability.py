"""Trace-context and request-timing middleware (Doc 01 section 1.8.2/1.8.3).

Every request receives a ``trace_id`` that is bound to the structlog context,
returned in the ``X-Trace-Id`` header, embedded in problem documents, and
propagated to the twin engine and agent runtime through command envelopes.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

TRACE_HEADER = "X-Trace-Id"
RequestHandler = Callable[[Request], Awaitable[Response]]


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Generate or propagate a trace id and bind it to the log context."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        incoming = request.headers.get(TRACE_HEADER)
        trace_id = incoming or uuid.uuid4().hex
        request.state.trace_id = trace_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Record request duration, emit a structured access log and Server-Timing."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"

        # Health probes are high-frequency and low-value; keep them at debug.
        log = logger.debug if request.url.path.startswith("/health") else logger.info
        log(
            "http_request",
            status=response.status_code,
            duration_ms=round(elapsed_ms, 2),
        )
        return response
