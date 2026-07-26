"""Fleet and engine read endpoints (Doc 12 sections 12.3 and 12.4).

Routers validate and delegate; they hold no business logic. The twin runner is
the source of truth and is injected via application state.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from at_api.services.twin_runner import TwinRunner
from at_core.domain.enums import CommandType, HealthBand
from at_core.errors import EngineNotFound, ValidationError

router = APIRouter(prefix="/api/v1", tags=["fleet"])


def get_runner(request: Request) -> TwinRunner:
    runner: TwinRunner | None = getattr(request.app.state, "twin_runner", None)
    if runner is None:  # pragma: no cover - configuration error
        raise EngineNotFound("Twin engine is not running.")
    return runner


RunnerDep = Annotated[TwinRunner, Depends(get_runner)]

SortKey = Literal["health", "rul", "cycle", "unit", "priority"]


@router.get("/fleet", summary="List the fleet")
async def list_fleet(
    runner: RunnerDep,
    sort: SortKey = "health",
    order: Literal["asc", "desc"] = "asc",
    band: str | None = Query(None, description="Comma-separated health bands"),
    search: str | None = Query(None, description="Match tail number or unit"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Paginated fleet listing with sorting and filtering."""
    snapshot = runner.fleet_snapshot()
    rows: list[dict[str, Any]] = list(snapshot.pop("engines_list", []))

    if band:
        wanted = {value.strip().upper() for value in band.split(",") if value.strip()}
        unknown = wanted - {member.value for member in HealthBand}
        if unknown:
            raise ValidationError(
                f"Unknown health band(s): {', '.join(sorted(unknown))}",
                errors=[{"field": "band", "message": "must be a valid health band"}],
            )
        rows = [row for row in rows if row.get("health_band") in wanted]

    if search:
        needle = search.lower()
        rows = [
            row
            for row in rows
            if needle in str(row.get("tail_number", "")).lower()
            or needle == str(row.get("unit_number"))
        ]

    key_map = {
        "health": lambda row: row.get("health_index") or 0.0,
        "rul": lambda row: row.get("rul_p50") if row.get("rul_p50") is not None else 1e9,
        "cycle": lambda row: row.get("cycle") or 0,
        "unit": lambda row: row.get("unit_number") or 0,
        # Priority: worst health first, tie-broken by shortest remaining life.
        "priority": lambda row: (
            row.get("health_index") or 0.0,
            row.get("rul_p50") if row.get("rul_p50") is not None else 1e9,
        ),
    }
    rows.sort(key=key_map[sort], reverse=(order == "desc"))

    total = len(rows)
    start = (page - 1) * size
    return {
        "items": rows[start : start + size],
        "page": page,
        "size": size,
        "total": total,
        "has_next": start + size < total,
        "aggregates": snapshot,
    }


@router.get("/fleet/summary", summary="Fleet KPIs")
async def fleet_summary_endpoint(runner: RunnerDep) -> dict[str, Any]:
    snapshot = runner.fleet_snapshot()
    snapshot.pop("engines_list", None)
    return snapshot


@router.get("/engines/{engine_ref}", tags=["engines"], summary="Twin detail")
async def get_engine(engine_ref: str, runner: RunnerDep) -> dict[str, Any]:
    """Full twin state. Accepts a UUID, a unit number, or an external ref."""
    detail = runner.twin_snapshot(_resolve(engine_ref, runner))
    if not detail:
        raise EngineNotFound(f"No engine matches '{engine_ref}'.")
    return detail


@router.post("/engines/{engine_ref}/commands/{command}", tags=["replay"], status_code=202)
async def send_command(
    engine_ref: str,
    command: str,
    runner: RunnerDep,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a control command. Asynchronous by design (single-writer rule)."""
    try:
        parsed = CommandType(command.upper())
    except ValueError as exc:
        raise ValidationError(
            f"Unknown command '{command}'.",
            errors=[
                {
                    "field": "command",
                    "message": f"must be one of {', '.join(c.value for c in CommandType)}",
                }
            ],
        ) from exc

    resolved = _resolve(engine_ref, runner)
    if resolved is None:
        raise EngineNotFound(f"No engine matches '{engine_ref}'.")

    await runner.submit(uuid.UUID(resolved), parsed, body or {})
    return {"accepted": True, "engine_id": resolved, "command": parsed.value}


@router.get("/system", tags=["admin"], summary="Engine runtime statistics")
async def system_stats(runner: RunnerDep) -> dict[str, Any]:
    return runner.stats()


def _resolve(engine_ref: str, runner: TwinRunner) -> str | None:
    """Resolve a UUID, unit number or external ref to an engine id."""
    try:
        return str(uuid.UUID(engine_ref))
    except ValueError:
        pass

    if engine_ref.isdigit():
        state = runner.registry.by_unit(int(engine_ref))
        return str(state.engine_id) if state else None

    for state in runner.registry.states():
        if engine_ref in (state.spec.external_ref, state.spec.tail_number):
            return str(state.engine_id)
    return None
