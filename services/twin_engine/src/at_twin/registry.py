"""Twin registry: owns every twin in a shard and drives the tick loop.

The registry is the single writer for twin state (Doc 01 section 1.5). Commands
arrive as data, telemetry arrives from a ``TelemetrySource``, and every mutation
flows through the pure transition functions in ``at_core.domain.twin``. The
registry itself holds no I/O: persistence and publication are handled by callers,
which keeps the whole engine testable in-process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
from at_data.regimes import RegimeModel

from at_core.domain.enums import CommandType, EngineModule, HealthBand, Subset, TwinStatus
from at_core.domain.health import ComponentState
from at_core.domain.twin import (
    EngineSpec,
    Prediction,
    TransitionResult,
    TwinState,
    apply_command,
    apply_health_update,
    apply_telemetry,
)
from at_core.events import DomainEvent
from at_twin.physics import (
    BaselineAccumulator,
    apply_maintenance,
    compute_component_health,
    compute_proxies,
)
from at_twin.replay import Cursor, ReplayClock, TelemetrySource, assign_phase_offsets


@dataclass(slots=True)
class TwinRuntime:
    """Everything the engine tracks for one twin beyond its domain state.

    Kept separate from ``TwinState`` because the domain state is an immutable
    value object that gets snapshotted and published, whereas this is engine
    bookkeeping that never leaves the process.
    """

    state: TwinState
    cursor: Cursor
    baseline: BaselineAccumulator = field(default_factory=BaselineAccumulator)
    components: dict[EngineModule, ComponentState] = field(default_factory=dict)
    last_snapshot_cycle: int = 0
    maintenance_pending: tuple[EngineModule, float] | None = None
    epoch_cycle: int = 0
    """Global clock reading when this twin last started or was recycled.

    Twin progress is measured relative to its own epoch rather than absolute
    clock time. Without this a recycled engine is permanently behind the global
    counter and never ages again.
    """


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of advancing the whole shard by one tick."""

    events: tuple[DomainEvent, ...] = ()
    advanced: tuple[uuid.UUID, ...] = ()
    duration_ms: float = 0.0

    @property
    def event_count(self) -> int:
        return len(self.events)


class TwinRegistry:
    """In-memory collection of twins for one shard."""

    def __init__(
        self,
        source: TelemetrySource,
        subset: Subset,
        *,
        split: str = "train",
        shard_index: int = 0,
        shard_count: int = 1,
        clock: ReplayClock | None = None,
        phase_seed: int | None = 42,
        snapshot_every: int = 50,
        recycle_on_failure: bool = True,
        regime_model: RegimeModel | None = None,
    ) -> None:
        if shard_count < 1 or not 0 <= shard_index < shard_count:
            raise ValueError(f"invalid shard {shard_index}/{shard_count}")

        self.source = source
        self.subset = subset
        self.split = split
        self.shard_index = shard_index
        self.shard_count = shard_count
        self.clock = clock or ReplayClock()
        self.snapshot_every = snapshot_every
        self.recycle_on_failure = recycle_on_failure
        self.regime_model = regime_model
        """Fitted k-means centroids from M2, used to classify live telemetry.

        Without this, multi-regime subsets fall back to a single regime and the
        per-regime baselines collapse into the pooled mean this design exists to
        avoid. It is optional only so single-regime subsets and tests can skip it.
        """
        """Replace a failed engine with a freshly installed one of the same unit.

        A real fleet has a standing population: engines are removed at overhaul
        and replaced, so the mix of ages persists. Without this the demo fleet
        monotonically dies and ends as 260 FAILED cards, which is both unrealistic
        and useless to look at. Failure events are still emitted -- the engine
        genuinely failed -- but the airframe gets a new engine and a new tail.
        """
        self._recycle_count: dict[uuid.UUID, int] = {}

        self._twins: dict[uuid.UUID, TwinRuntime] = {}
        self._by_unit: dict[int, uuid.UUID] = {}

        self._provision(phase_seed)

    # ── provisioning ─────────────────────────────────────────────────────────

    def _provision(self, phase_seed: int | None) -> None:
        """Create a twin for every unit belonging to this shard."""
        units = tuple(
            unit for unit in self.source.units() if unit % self.shard_count == self.shard_index
        )
        lengths = {unit: self.source.length(unit) for unit in units}
        offsets = (
            assign_phase_offsets(units, lengths, seed=phase_seed)
            if phase_seed is not None
            else dict.fromkeys(units, 0)
        )

        for unit in units:
            # Deterministic id derived from identity, so a restart or a reseed
            # produces the same engine ids and any persisted rows still match.
            engine_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"aerotwin/{self.subset.value}/{self.split}/{unit}",
            )
            spec = EngineSpec(
                engine_id=engine_id,
                unit_number=unit,
                subset=self.subset,
                split=self.split,
                total_cycles=lengths[unit],
                tail_number=f"AT-{unit:04d}",
            )
            self._twins[engine_id] = TwinRuntime(
                state=TwinState(spec=spec),
                cursor=Cursor(
                    unit=unit,
                    cycle=0,
                    total_cycles=lengths[unit],
                    phase_offset=offsets[unit],
                ),
            )
            self._by_unit[unit] = engine_id

    # ── accessors ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._twins)

    @property
    def engine_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(self._twins)

    def get(self, engine_id: uuid.UUID) -> TwinState | None:
        runtime = self._twins.get(engine_id)
        return runtime.state if runtime else None

    def by_unit(self, unit: int) -> TwinState | None:
        engine_id = self._by_unit.get(unit)
        return self.get(engine_id) if engine_id else None

    def states(self) -> tuple[TwinState, ...]:
        return tuple(runtime.state for runtime in self._twins.values())

    def active_count(self) -> int:
        return sum(1 for runtime in self._twins.values() if runtime.state.is_active)

    def band_counts(self) -> dict[HealthBand, int]:
        counts = dict.fromkeys(HealthBand, 0)
        for runtime in self._twins.values():
            counts[runtime.state.health_band] += 1
        return counts

    # ── commands ─────────────────────────────────────────────────────────────

    def start_all(self, now_ms: float = 0.0) -> tuple[DomainEvent, ...]:
        """Start every idle twin at its phase offset."""
        events: list[DomainEvent] = []
        for engine_id in self._twins:
            events.extend(self.command(engine_id, CommandType.START, now_ms=now_ms))
        return tuple(events)

    def command(
        self,
        engine_id: uuid.UUID,
        command: CommandType,
        args: dict[str, object] | None = None,
        *,
        now_ms: float = 0.0,
    ) -> tuple[DomainEvent, ...]:
        """Apply a control command to one twin.

        Invalid transitions are rejected via an event rather than an exception,
        so a bad command from the API can never destabilise the tick loop.
        """
        runtime = self._twins.get(engine_id)
        if runtime is None:
            return ()

        args = args or {}
        result: TransitionResult = apply_command(runtime.state, command, args)
        runtime.state = result.state
        events = list(result.events)

        was_rejected = any(
            event.event_type.value == "twin.command.rejected" for event in result.events
        )
        if was_rejected:
            return tuple(events)

        if command is CommandType.START and runtime.cursor.cycle == 0:
            # Jump to the staggered starting position so the fleet spans a
            # realistic range of ages from the first tick.
            runtime.cursor = runtime.cursor.seek(runtime.cursor.phase_offset)
            runtime.epoch_cycle = int(self.clock.cycles_at(now_ms))
            events.extend(self._catch_up_baseline(runtime))

        elif command is CommandType.SEEK:
            raw = args.get("cycle", runtime.cursor.cycle)
            target = raw if isinstance(raw, int) else runtime.cursor.cycle
            runtime.cursor = runtime.cursor.seek(target)
            runtime.baseline = BaselineAccumulator()
            runtime.components = {}
            events.extend(self._catch_up_baseline(runtime))

        elif command is CommandType.RESET:
            runtime.cursor = runtime.cursor.seek(0)
            runtime.baseline = BaselineAccumulator()
            runtime.components = {}
            runtime.last_snapshot_cycle = 0

        elif command is CommandType.SET_SPEED:
            raw_speed = args.get("speed", self.clock.speed)
            if isinstance(raw_speed, int | float):
                self.clock = self.clock.with_speed(float(raw_speed), now_ms)

        elif command is CommandType.PERFORM_MAINTENANCE:
            module_name = args.get("module")
            effectiveness = args.get("effectiveness", 0.6)
            if isinstance(module_name, str):
                runtime.maintenance_pending = (
                    EngineModule(module_name),
                    float(effectiveness) if isinstance(effectiveness, int | float) else 0.6,
                )

        return tuple(events)

    def _catch_up_baseline(self, runtime: TwinRuntime) -> tuple[DomainEvent, ...]:
        """Establish the healthy baseline from the engine's opening cycles.

        A twin starting at a phase offset has not observed its own early life, so
        its baseline is built from the first cycles of the trajectory. Without
        this, a mid-life engine would appear perfectly healthy because its
        baseline was taken at mid-life.
        """
        from at_twin.physics import BASELINE_CYCLES

        baseline = BaselineAccumulator()
        # Multi-regime subsets need enough opening cycles to fill a baseline for
        # each of the six flight conditions, not just the first twenty rows.
        span = BASELINE_CYCLES * max(1, self.subset.n_conditions)
        for cycle in range(1, span + 1):
            row = self.source.read(runtime.cursor.unit, cycle)
            if row is None:
                break
            baseline = baseline.observe(
                compute_proxies(row.sensors), self._classify_regime(row.op_settings)
            )
        runtime.baseline = baseline
        return ()

    # ── tick ─────────────────────────────────────────────────────────────────

    def tick(self, now_ms: float, *, wall_ts: datetime | None = None) -> TickResult:
        """Advance every active twin to the cycle implied by the clock."""
        import time

        started = time.perf_counter()
        target = int(self.clock.cycles_at(now_ms))
        stamp = wall_ts or datetime.now(UTC)

        events: list[DomainEvent] = []
        advanced: list[uuid.UUID] = []

        for engine_id, runtime in self._twins.items():
            if not runtime.state.is_active:
                continue

            desired = runtime.cursor.phase_offset + (target - runtime.epoch_cycle)
            if desired <= runtime.cursor.cycle:
                continue

            before = runtime.cursor.cycle
            produced = self._advance_one(runtime, desired, stamp)
            events.extend(produced)
            # Track advancement by cursor movement, not by event emission: most
            # cycles legitimately produce no events (health changes are coalesced
            # and only band *transitions* are eventful), but the twin still moved
            # and still needs publishing.
            if runtime.cursor.cycle != before:
                advanced.append(engine_id)

            if self.recycle_on_failure and runtime.state.status is TwinStatus.FAILED:
                events.extend(self._recycle(engine_id, runtime, stamp, now_cycle=target))

        return TickResult(
            events=tuple(events),
            advanced=tuple(advanced),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _advance_one(
        self, runtime: TwinRuntime, target_cycle: int, stamp: datetime
    ) -> list[DomainEvent]:
        """Advance one twin to ``target_cycle``, emitting the resulting events.

        Only the final cycle of a multi-cycle jump produces a health update: at
        32x speed a tick can span several cycles, and recomputing health for each
        intermediate cycle would triple the work for output nobody observes.
        """
        events: list[DomainEvent] = []
        cursor = runtime.cursor.advance_to(target_cycle)
        if cursor.cycle == runtime.cursor.cycle:
            return events

        row = self.source.read(cursor.unit, cursor.cycle)
        if row is None:
            # Trajectory exhausted: drive the twin to FAILED through the domain.
            result = apply_telemetry(
                runtime.state,
                cycle=cursor.total_cycles,
                sensors=dict(runtime.state.sensors),
                op_settings=runtime.state.op_settings,
                regime=runtime.state.regime,
                wall_ts=stamp,
            )
            runtime.state = result.state
            runtime.cursor = cursor
            return list(result.events)

        runtime.cursor = cursor

        telemetry = apply_telemetry(
            runtime.state,
            cycle=cursor.cycle,
            sensors=row.sensors,
            op_settings=row.op_settings,
            regime=self._classify_regime(row.op_settings),
            wall_ts=stamp,
        )
        runtime.state = telemetry.state
        events.extend(telemetry.events)

        regime = runtime.state.regime
        if not runtime.baseline.is_complete:
            runtime.baseline = runtime.baseline.observe(compute_proxies(row.sensors), regime)

        components = compute_component_health(
            row.sensors,
            runtime.baseline,
            previous=runtime.components or None,
            cycle=cursor.cycle,
            regime=regime,
        )

        maintenance_applied = False
        if runtime.maintenance_pending is not None:
            module, effectiveness = runtime.maintenance_pending
            components = apply_maintenance(components, module, effectiveness, cursor.cycle)
            runtime.maintenance_pending = None
            maintenance_applied = True

        runtime.components = components

        prediction = self._estimate_prediction(runtime, components)
        health = apply_health_update(
            runtime.state,
            components=components,
            anomaly_score=runtime.state.anomaly_score,
            prediction=prediction,
            # Until M5 the RUL figure is a trend extrapolation, not a model
            # output. It is displayed but must not drive the health index.
            model_trusted=prediction is not None and prediction.model_id is not None,
            maintenance_applied=maintenance_applied,
        )
        runtime.state = health.state
        events.extend(health.events)

        return events

    def _recycle(
        self,
        engine_id: uuid.UUID,
        runtime: TwinRuntime,
        stamp: datetime,
        now_cycle: int = 0,
    ) -> list[DomainEvent]:
        """Replace a failed engine with a fresh install of the same unit.

        Resets the twin to cycle zero with a new tail number and a clean baseline,
        preserving the event sequence so the audit log stays continuous.
        """
        generation = self._recycle_count.get(engine_id, 0) + 1
        self._recycle_count[engine_id] = generation

        spec = replace(
            runtime.state.spec,
            tail_number=f"AT-{runtime.cursor.unit:04d}-{generation}",
        )
        runtime.state = TwinState(spec=spec, seq=runtime.state.seq, status=TwinStatus.RUNNING)
        runtime.cursor = runtime.cursor.seek(0)
        runtime.cursor = replace(runtime.cursor, phase_offset=0)
        runtime.components = {}
        runtime.last_snapshot_cycle = 0
        runtime.maintenance_pending = None
        runtime.epoch_cycle = now_cycle
        self._catch_up_baseline(runtime)
        return []

    def _classify_regime(self, op_settings: tuple[float, float, float]) -> int:
        """Classify the operating condition from the three op settings."""
        if self.subset.n_conditions == 1 or self.regime_model is None:
            return 0
        return int(self.regime_model.predict(np.asarray([op_settings]))[0])

    def _estimate_prediction(
        self, runtime: TwinRuntime, components: dict[EngineModule, ComponentState]
    ) -> Prediction | None:
        """Provisional RUL estimate until the trained model lands in M5.

        Extrapolates the worst component's degradation rate to zero. This is a
        placeholder with an honest name -- it is never presented as a model
        prediction, and ``model_id`` is explicitly ``None`` so the UI can show
        that no model is attached yet.
        """
        if not components or not runtime.baseline.is_ready:
            return None

        worst = min(components.values(), key=lambda component: component.score)
        rate = worst.degradation_rate
        estimate = 125.0 if rate >= -1e-6 else min(125.0, max(0.0, worst.score / abs(rate)))

        return Prediction(
            rul_p50=estimate,
            rul_p10=estimate * 0.6,
            rul_p90=estimate * 1.5,
            model_id=None,
            computed_at_cycle=runtime.state.cycle,
        )

    # ── snapshots ────────────────────────────────────────────────────────────

    def due_for_snapshot(self) -> tuple[TwinState, ...]:
        """Twins that have advanced far enough to warrant a new snapshot."""
        due: list[TwinState] = []
        for runtime in self._twins.values():
            if runtime.state.cycle - runtime.last_snapshot_cycle >= self.snapshot_every:
                runtime.last_snapshot_cycle = runtime.state.cycle
                due.append(runtime.state)
        return tuple(due)

    def rehydrate(self, engine_id: uuid.UUID, state: TwinState, cursor_cycle: int) -> None:
        """Restore a twin from a persisted snapshot after a restart."""
        runtime = self._twins.get(engine_id)
        if runtime is None:
            return
        runtime.state = state
        runtime.cursor = runtime.cursor.seek(cursor_cycle)
        runtime.last_snapshot_cycle = state.cycle
        self._catch_up_baseline(runtime)


@dataclass(frozen=True, slots=True)
class FleetSummary:
    """Aggregate fleet KPIs.

    A typed structure rather than a loose dict, so consumers (the monitor now, the
    REST API in M4) get compile-time checking instead of stringly-typed lookups.
    """

    engines: int = 0
    active: int = 0
    failed: int = 0
    avg_health: float = 0.0
    by_band: dict[str, int] = field(default_factory=dict)
    at_risk: int = 0
    speed: float = 1.0

    def as_dict(self) -> dict[str, object]:
        """Serialise for the bus and the REST layer."""
        return {
            "engines": self.engines,
            "active": self.active,
            "failed": self.failed,
            "avg_health": round(self.avg_health, 2),
            "by_band": dict(self.by_band),
            "at_risk": self.at_risk,
            "speed": self.speed,
        }


def fleet_summary(registry: TwinRegistry) -> FleetSummary:
    """Aggregate fleet KPIs for the dashboard and the terminal monitor."""
    states = registry.states()
    if not states:
        return FleetSummary()

    counts = registry.band_counts()
    return FleetSummary(
        engines=len(states),
        active=registry.active_count(),
        failed=sum(1 for state in states if state.status is TwinStatus.FAILED),
        avg_health=sum(state.health_index for state in states) / len(states),
        by_band={band.value: count for band, count in counts.items()},
        at_risk=sum(
            1 for state in states if state.health_band in (HealthBand.WARNING, HealthBand.CRITICAL)
        ),
        speed=registry.clock.speed,
    )
