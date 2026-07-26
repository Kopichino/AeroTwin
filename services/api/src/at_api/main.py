"""FastAPI application factory (Doc 05 section 5.2).

Middleware order, outermost to innermost:
    1. TraceContextMiddleware   -- trace id generation and log binding
    2. CORSMiddleware           -- browser access control
    3. GZipMiddleware           -- response compression
    4. RequestTimingMiddleware  -- duration measurement and access logging
    (5. RateLimit, 6. Authentication -- added in M6 alongside the auth router)

Exception handlers are registered last so they wrap everything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from at_api.middleware import (
    RequestTimingMiddleware,
    TraceContextMiddleware,
    register_exception_handlers,
)
from at_api.routers import health
from at_config import Settings, get_settings
from at_observability import configure_logging

logger = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"

DESCRIPTION = """
Agentic Digital Twin Platform for Aircraft Engine Predictive Maintenance.

Replays the NASA C-MAPSS turbofan degradation dataset as live telemetry, maintains
an event-sourced digital twin per engine, predicts Remaining Useful Life with deep
models, detects anomalies, explains predictions, and reasons over the fleet with a
LangGraph multi-agent system.
""".strip()

TAGS_METADATA: list[dict[str, Any]] = [
    {"name": "health", "description": "Liveness, readiness and deep diagnostics."},
    {"name": "fleet", "description": "Fleet-wide listing, ranking and aggregates."},
    {"name": "engines", "description": "Individual twin state, telemetry and timeline."},
    {"name": "predictions", "description": "RUL predictions and explanations."},
    {"name": "anomalies", "description": "Anomaly detections and acknowledgement."},
    {"name": "replay", "description": "Replay clock control (asynchronous commands)."},
    {"name": "simulate", "description": "What-if scenario simulation."},
    {"name": "copilot", "description": "Conversational engineering assistant."},
    {"name": "agents", "description": "Agent runs, traces and topology."},
    {"name": "knowledge", "description": "RAG corpus search and documents."},
    {"name": "maintenance", "description": "Work packages and scheduling."},
    {"name": "admin", "description": "Operator tooling and system state."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Accepting an optional ``Settings`` makes the factory directly usable from
    tests with an arbitrary profile, with no environment mutation required.
    """
    settings = settings or get_settings()

    configure_logging(
        service_name=settings.service_name,
        level=settings.log_level,
        json_output=settings.log_json,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Acquire and release process-wide resources.

        M2 adds the asyncpg pool, M3 the Redis bus and WebSocket broadcaster,
        M5 the model-registry warmup.
        """
        logger.info(
            "service_starting",
            profile=settings.profile.value,
            version=settings.version,
            llm_provider=settings.llm_provider.value,
        )
        app.state.settings = settings
        try:
            yield
        finally:
            logger.info("service_stopped")

    app = FastAPI(
        title="AeroTwin API",
        description=DESCRIPTION,
        version=settings.version,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        # Problem documents are produced by our own handlers.
        responses={
            400: {"description": "Validation failed"},
            500: {"description": "Internal error"},
        },
    )

    # Settings must be available even when the app is used without the lifespan
    # (for example, TestClient construction in unit tests).
    app.state.settings = settings

    # ── middleware (added innermost-first; Starlette reverses the order) ──────
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id", "Server-Timing"],
    )
    app.add_middleware(TraceContextMiddleware)

    register_exception_handlers(app)

    # ── routers ──────────────────────────────────────────────────────────────
    app.include_router(health.router)  # unprefixed: probes live at /health/*

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "AeroTwin API",
            "version": settings.version,
            "docs": "/docs",
            "health": "/health/ready",
        }

    return app


app = create_app()
