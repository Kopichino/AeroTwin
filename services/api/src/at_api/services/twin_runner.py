"""Twin engine runner hosted inside the API process.

With the in-memory bus (ADR-002 adapter A) the twin engine runs as an asyncio
task alongside FastAPI, which means the whole platform is a single ``uvicorn``
command with no external services. Against Redis the identical code runs as a
separate process; only the bus adapter and the composition root differ.

The single-writer invariant (Doc 01 section 1.5) is preserved either way: this
runner is the only thing that mutates twin state, commands arrive through a
queue, and everything else reads published snapshots.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import structlog

from at_bus import Envelope, EventBus, twin_channel
from at_bus.ports import CHANNEL_FLEET, CHANNEL_SYSTEM
from at_core.domain.enums import CommandType, Subset
from at_twin.registry import TwinRegistry, fleet_summary
from at_twin.replay import ReplayClock

logger = structlog.get_logger(__name__)

#: Per-engine delta publication ceiling (Doc 13 section 13.5). The engine may
#: tick faster; frames are coalesced down to this rate per engine.
PUBLISH_MAX_HZ = 4.0

#: Fleet rollup rate. One frame carries every changed engine, so this stays
#: cheap even with 260 twins.
FLEET_PUBLISH_HZ = 1.0


class TwinRunner:
    """Owns the twin registry and drives the tick loop."""

    def __init__(
        self,
        registry: TwinRegistry,
        bus: EventBus,
        *,
        tick_hz: float = 8.0,
        publish_max_hz: float = PUBLISH_MAX_HZ,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.tick_interval = 1.0 / tick_hz
        self.publish_interval = 1.0 / publish_max_hz
        self.fleet_interval = 1.0 / FLEET_PUBLISH_HZ

        self._task: asyncio.Task[None] | None = None
        self._commands: asyncio.Queue[tuple[uuid.UUID, CommandType, dict[str, Any]]] = (
            asyncio.Queue(maxsize=1000)
        )
        self._started_at = 0.0
        self._last_publish: dict[uuid.UUID, float] = {}
        self._last_fleet_publish = 0.0
        self._tick_latencies: list[float] = []

        self.ticks = 0
        self.frames_published = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._started_at = time.perf_counter()
        self.registry.start_all(0.0)
        self._task = asyncio.create_task(self._run(), name="twin-runner")
        logger.info(
            "twin_runner_started",
            engines=len(self.registry),
            subset=self.registry.subset.value,
            speed=self.registry.clock.speed,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("twin_runner_stopped", ticks=self.ticks)

    # ── commands ─────────────────────────────────────────────────────────────

    async def submit(
        self, engine_id: uuid.UUID, command: CommandType, args: dict[str, Any] | None = None
    ) -> None:
        """Queue a command. Applied by the tick loop, never inline."""
        with contextlib.suppress(asyncio.QueueFull):
            self._commands.put_nowait((engine_id, command, args or {}))

    def _drain_commands(self, now_ms: float) -> list[Any]:
        events: list[Any] = []
        while True:
            try:
                engine_id, command, args = self._commands.get_nowait()
            except asyncio.QueueEmpty:
                return events
            events.extend(self.registry.command(engine_id, command, args, now_ms=now_ms))

    # ── main loop ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            loop_started = time.perf_counter()
            elapsed = loop_started - self._started_at
            now_ms = elapsed * 1000.0

            try:
                events = self._drain_commands(now_ms)
                result = self.registry.tick(now_ms)
                events.extend(result.events)

                self.ticks += 1
                self._tick_latencies.append(result.duration_ms)
                if len(self._tick_latencies) > 500:
                    del self._tick_latencies[:250]

                await self._publish(result.advanced, events, loop_started)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - keep the loop alive
                logger.exception("tick_failed", error=str(exc))

            # Drift-corrected sleep: if a tick overran, skip ahead rather than
            # accumulating a backlog (P7).
            remaining = self.tick_interval - (time.perf_counter() - loop_started)
            await asyncio.sleep(max(0.0, remaining))

    async def _publish(
        self, advanced: tuple[uuid.UUID, ...], events: list[Any], now: float
    ) -> None:
        """Emit coalesced per-engine deltas, domain events and a fleet rollup."""
        for engine_id in advanced:
            last = self._last_publish.get(engine_id, 0.0)
            if now - last < self.publish_interval:
                continue
            state = self.registry.get(engine_id)
            if state is None:
                continue
            self._last_publish[engine_id] = now
            await self.bus.publish(
                twin_channel(engine_id),
                Envelope(type="twin.delta", payload=_delta(self.registry, engine_id)),
            )
            self.frames_published += 1

        for event in events:
            await self.bus.publish(
                twin_channel(event.engine_id),
                Envelope(
                    type="twin.event",
                    payload={
                        "engine_id": str(event.engine_id),
                        "seq": event.seq,
                        "cycle": event.cycle,
                        "event_type": event.event_type.value,
                        "severity": event.severity.value,
                        "payload": event.payload,
                    },
                ),
            )
            self.frames_published += 1

        if now - self._last_fleet_publish >= self.fleet_interval:
            self._last_fleet_publish = now
            await self.bus.publish(
                CHANNEL_FLEET,
                Envelope(type="fleet.delta", payload=self.fleet_snapshot()),
            )
            await self.bus.publish(
                CHANNEL_SYSTEM,
                Envelope(type="system.status", payload=self.stats()),
            )
            self.frames_published += 2

    # ── snapshots for the gateway ────────────────────────────────────────────

    def fleet_snapshot(self, _: str | None = None) -> dict[str, Any]:
        summary = fleet_summary(self.registry).as_dict()
        summary["engines_list"] = [
            _row(self.registry, state.engine_id)
            for state in sorted(self.registry.states(), key=lambda s: s.health_index)
        ]
        summary["cycle"] = int(
            self.registry.clock.cycles_at((time.perf_counter() - self._started_at) * 1000.0)
        )
        return summary

    def twin_snapshot(self, engine_ref: str | None) -> dict[str, Any] | None:
        if engine_ref is None:
            return None
        try:
            engine_id = uuid.UUID(engine_ref)
        except ValueError:
            state = self.registry.by_unit(int(engine_ref)) if engine_ref.isdigit() else None
            if state is None:
                return None
            engine_id = state.engine_id
        return _delta(self.registry, engine_id)

    def system_snapshot(self, _: str | None = None) -> dict[str, Any]:
        return self.stats()

    def stats(self) -> dict[str, Any]:
        latencies = sorted(self._tick_latencies)
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0
        return {
            "ticks": self.ticks,
            "frames_published": self.frames_published,
            "engines": len(self.registry),
            "active": self.registry.active_count(),
            "speed": self.registry.clock.speed,
            "tick_p50_ms": round(latencies[len(latencies) // 2], 3) if latencies else 0.0,
            "tick_p99_ms": round(p99, 3),
            "uptime_s": round(time.perf_counter() - self._started_at, 1),
            "inference": self.registry.inference.stats(),
        }


def _delta(registry: TwinRegistry, engine_id: uuid.UUID) -> dict[str, Any]:
    """Full per-engine payload. Deltas are state-replacing, so this is complete."""
    state = registry.get(engine_id)
    if state is None:
        return {}
    runtime = registry._twins[engine_id]

    components = {
        module.value: round(component.score, 2)
        for module, component in sorted(runtime.components.items(), key=lambda item: item[0].value)
    }
    worst = (
        min(runtime.components.items(), key=lambda item: item[1].score)
        if runtime.components
        else None
    )

    anomaly = runtime.anomaly
    return {
        "engine_id": str(engine_id),
        "external_ref": state.spec.external_ref,
        "tail_number": state.spec.tail_number,
        "unit_number": state.spec.unit_number,
        "status": state.status.value,
        "cycle": state.cycle,
        "total_cycles": state.spec.total_cycles,
        "progress": round(state.progress, 4),
        "health_index": round(state.health_index, 2),
        "health_band": state.health_band.value,
        "degradation_rate": round(state.degradation_rate, 4),
        "regime": state.regime,
        "components": components,
        "worst_module": worst[0].value if worst else None,
        "drivers": list(worst[1].drivers[:4]) if worst else [],
        "rul_p50": round(state.prediction.rul_p50, 1) if state.prediction else None,
        "rul_p10": round(state.prediction.rul_p10 or 0.0, 1) if state.prediction else None,
        "rul_p90": round(state.prediction.rul_p90 or 0.0, 1) if state.prediction else None,
        "model_id": state.prediction.model_id if state.prediction else None,
        "anomaly_score": round(state.anomaly_score, 3),
        "anomaly": (
            {
                "score": round(anomaly.score, 2),
                "severity": anomaly.severity.value,
                "detector": anomaly.detector,
                "module": anomaly.module.value if anomaly.module else None,
                "alerting": anomaly.is_alerting,
                "sensors": [
                    {"sensor": key, "z": round(value, 2)} for key, value in anomaly.sensors[:5]
                ],
            }
            if anomaly
            else None
        ),
        "failure_prob": (
            {str(k): round(v, 3) for k, v in state.prediction.failure_prob.items()}
            if state.prediction
            else {}
        ),
        "prediction_stale": state.prediction.stale if state.prediction else False,
        "sensors": {key: round(value, 3) for key, value in state.sensors.items()},
        "seq": state.seq,
    }


def _row(registry: TwinRegistry, engine_id: uuid.UUID) -> dict[str, Any]:
    """Compact row for the fleet grid."""
    state = registry.get(engine_id)
    if state is None:
        return {}
    runtime = registry._twins[engine_id]
    worst = (
        min(runtime.components.items(), key=lambda item: item[1].score)[0].value
        if runtime.components
        else None
    )
    anomaly = runtime.anomaly
    prediction = state.prediction
    return {
        "engine_id": str(engine_id),
        "tail_number": state.spec.tail_number,
        "unit_number": state.spec.unit_number,
        "status": state.status.value,
        "cycle": state.cycle,
        "health_index": round(state.health_index, 2),
        "health_band": state.health_band.value,
        "worst_module": worst,
        "rul_p50": round(prediction.rul_p50, 1) if prediction else None,
        "rul_p10": round(prediction.rul_p10 or 0.0, 1) if prediction else None,
        "rul_p90": round(prediction.rul_p90 or 0.0, 1) if prediction else None,
        "model_backed": bool(prediction and prediction.model_id),
        "anomaly_score": round(anomaly.score, 2) if anomaly else 0.0,
        "anomaly_alerting": bool(anomaly and anomaly.is_alerting),
    }


def build_registry(
    subset: Subset,
    interim_dir: Any,
    *,
    speed: float = 8.0,
    synthetic: bool = False,
) -> TwinRegistry:
    """Compose a registry, preferring real data and falling back to synthetic."""
    from pathlib import Path

    from at_twin.replay import CmapssFileSource, SyntheticSource

    interim = Path(interim_dir)
    regime_model = None

    if synthetic:
        source: Any = SyntheticSource(n_units=24, length=200)
    else:
        try:
            source = CmapssFileSource(subset, interim, "train")
        except FileNotFoundError:
            logger.warning("dataset_missing_using_synthetic", interim=str(interim))
            source = SyntheticSource(n_units=24, length=200)
        else:
            regimes_path = interim.parent / "processed" / "regimes.json"
            if subset.n_conditions > 1 and regimes_path.is_file():
                from at_data.regimes import load_models

                regime_model = load_models(regimes_path).get(subset)

    from at_twin.inference import InferenceClient, load_production_models

    # Load whatever the registry has promoted to PRODUCTION. An empty registry is
    # not an error: the twin falls back to trend extrapolation and marks the
    # prediction as model-less so the UI can say so honestly.
    models = load_production_models(Path("models/registry.json"))
    if models:
        logger.info("models_loaded", subsets=sorted(models), count=len(models))
    else:
        logger.warning("no_production_models", detail="RUL will use trend fallback")

    return TwinRegistry(
        source,
        subset,
        clock=ReplayClock(speed=speed),
        phase_seed=42,
        regime_model=regime_model,
        inference=InferenceClient(models=models),
    )
