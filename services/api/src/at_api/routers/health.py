"""Liveness, readiness and deep health probes (Doc 05 section 5.7).

``/health/live``  -- process is up. Never touches dependencies.
``/health/ready`` -- dependencies reachable. Used by compose/k8s gates.
``/health/deep``  -- full diagnostic including shard leases (admin only in M3+).
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from at_api.deps import get_app_settings
from at_config import Settings

router = APIRouter(prefix="/health", tags=["health"])

_STARTED_AT = time.time()


class ComponentStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    SKIPPED = "skipped"


class DependencyHealth(BaseModel):
    name: str
    status: ComponentStatus
    latency_ms: float | None = None
    detail: str | None = None


class LivenessResponse(BaseModel):
    status: ComponentStatus = ComponentStatus.OK
    service: str
    version: str
    uptime_s: float = Field(description="Seconds since process start")


class ReadinessResponse(BaseModel):
    status: ComponentStatus
    service: str
    version: str
    profile: str
    dependencies: list[DependencyHealth]


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> LivenessResponse:
    """Return 200 whenever the process is running and the event loop is responsive."""
    return LivenessResponse(
        service=settings.service_name,
        version=settings.version,
        uptime_s=round(time.time() - _STARTED_AT, 3),
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ReadinessResponse:
    """Check every backing dependency.

    In M1 the datastores are not yet wired, so they report ``skipped`` rather
    than faking success. M2 replaces these with real connection checks.
    """
    dependencies = [
        DependencyHealth(
            name="postgres",
            status=ComponentStatus.SKIPPED,
            detail="wired in M2",
        ),
        DependencyHealth(
            name="redis",
            status=ComponentStatus.SKIPPED,
            detail="wired in M3",
        ),
        DependencyHealth(
            name="inference",
            status=ComponentStatus.SKIPPED,
            detail="wired in M5",
        ),
    ]

    overall = (
        ComponentStatus.DOWN
        if any(dep.status is ComponentStatus.DOWN for dep in dependencies)
        else ComponentStatus.OK
    )
    if overall is ComponentStatus.DOWN:
        response.status_code = 503

    return ReadinessResponse(
        status=overall,
        service=settings.service_name,
        version=settings.version,
        profile=settings.profile.value,
        dependencies=dependencies,
    )


@router.get("/deep", summary="Deep diagnostic")
async def deep(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, Any]:
    """Expose runtime configuration and subsystem state for operators.

    Secrets are never included -- only whether they are configured.
    """
    return {
        "service": settings.service_name,
        "version": settings.version,
        "profile": settings.profile.value,
        "uptime_s": round(time.time() - _STARTED_AT, 3),
        "replay": {
            "speed": settings.replay_speed,
            "cycle_duration_ms": settings.cycle_duration_ms,
            "shard": f"{settings.shard_index}/{settings.shard_count}",
        },
        "ml": {
            "registry_path": settings.model_registry_path,
            "explain_every_cycles": settings.explain_every_cycles,
        },
        "agents": {
            "provider": settings.llm_provider.value,
            "llm_enabled": settings.llm_enabled,
            "api_key_configured": settings.llm_api_key is not None,
            "wall_timeout_s": settings.agent_wall_timeout_s,
        },
        "milestone": "M1",
    }
