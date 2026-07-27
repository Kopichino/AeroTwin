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
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from at_api.middleware import (
    RequestTimingMiddleware,
    TraceContextMiddleware,
    register_exception_handlers,
)
from at_api.routers import fleet, health, knowledge
from at_api.services.twin_runner import TwinRunner, build_registry
from at_api.ws.gateway import WebSocketGateway
from at_bus import InMemoryBus
from at_bus.ports import CHANNEL_FLEET, CHANNEL_SYSTEM
from at_config import Settings, get_settings
from at_core.domain.enums import Subset
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

    # Resolved here rather than inside the lifespan coroutine: a blocking
    # filesystem probe does not belong on the event loop, even at startup.
    corpus = Path(settings.knowledge_dir)
    corpus_exists = corpus.is_dir()

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

        # Adapter selection is the only difference between the single-process
        # demo and the Redis-backed Docker stack (ADR-002).
        bus = InMemoryBus()
        app.state.bus = bus

        registry = build_registry(
            Subset(settings.twin_subset),
            settings.interim_dir,
            speed=settings.replay_speed,
            synthetic=settings.twin_synthetic,
        )
        runner = TwinRunner(registry, bus, tick_hz=settings.tick_hz)
        app.state.twin_runner = runner

        gateway = WebSocketGateway(bus)
        gateway.register_snapshot_provider(CHANNEL_FLEET, runner.fleet_snapshot)
        gateway.register_snapshot_provider("twin", runner.twin_snapshot)
        gateway.register_snapshot_provider(CHANNEL_SYSTEM, runner.system_snapshot)
        app.state.ws_gateway = gateway

        # The knowledge index is built once at startup: loading the embedding
        # model takes several seconds, so per-request construction would make
        # search unusable and lazy construction would penalise one unlucky user.
        # A corpus failure must not prevent the platform from starting -- the
        # fleet does not depend on it, and search reports its own unavailability.
        app.state.knowledge_index = None
        if corpus_exists:
            try:
                from at_rag.index import build_index

                index = build_index(corpus)
                app.state.knowledge_index = index
                logger.info("knowledge_indexed", **index.stats())
            except Exception as exc:
                logger.warning("knowledge_index_failed", error=str(exc))
        else:
            logger.warning("knowledge_corpus_missing", path=str(corpus))

        await runner.start()
        try:
            yield
        finally:
            await runner.stop()
            await bus.close()
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
    app.include_router(fleet.router)
    app.include_router(knowledge.router)

    @app.websocket("/ws/v1")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        gateway: WebSocketGateway = websocket.app.state.ws_gateway
        await gateway.handle(websocket)

    dashboard = Path(__file__).parent / "static" / "index.html"

    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard() -> FileResponse:
        """Zero-build live dashboard.

        A single self-contained HTML file so the streaming stack is visible
        immediately, without waiting for the Next.js frontend in M6.
        """
        return FileResponse(dashboard)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "AeroTwin API",
            "version": settings.version,
            "docs": "/docs",
            "dashboard": "/dashboard",
            "health": "/health/ready",
        }

    return app


app = create_app()
