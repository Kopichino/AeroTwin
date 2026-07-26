"""The DigitalTwin aggregate: identity, state and pure state transitions.

Per Doc 03 section 3.6, this module contains no ``await``, no I/O and no unseeded
randomness. Every transition is a pure function ``(state, input) -> (state', events)``
which makes the entire degradation model deterministic and testable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType

from at_core.domain.enums import (
    CommandType,
    EngineModule,
    HealthBand,
    Severity,
    Subset,
    TwinStatus,
)
from at_core.domain.fsm import can_apply, next_status, rejection_reason
from at_core.domain.health import (
    BandTracker,
    ComponentState,
    HealthInputs,
    fuse_health_index,
)
from at_core.events.types import DomainEvent, EventType

EngineId = uuid.UUID


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """Immutable identity and provenance of one engine."""

    engine_id: EngineId
    unit_number: int
    subset: Subset
    split: str
    total_cycles: int
    tail_number: str | None = None
    engine_model: str = "AT-9000"
    true_rul: int | None = None

    @property
    def external_ref(self) -> str:
        """Stable human-facing reference, e.g. ``FD001-train-U27``."""
        return f"{self.subset.value}-{self.split}-U{self.unit_number}"


@dataclass(frozen=True, slots=True)
class Prediction:
    """A single RUL prediction with its uncertainty interval."""

    rul_p50: float
    rul_p10: float | None = None
    rul_p90: float | None = None
    failure_prob: MappingProxyType[int, float] = field(default_factory=lambda: MappingProxyType({}))
    model_id: str | None = None
    computed_at_cycle: int = 0
    stale: bool = False


@dataclass(frozen=True, slots=True)
class TwinState:
    """Complete state of one digital twin at a point in its life.

    Instances are immutable; transitions return a new instance. ``seq`` is the
    per-engine monotonic event sequence used for event-sourced rehydration
    (Doc 08 section 8.2).
    """

    spec: EngineSpec
    status: TwinStatus = TwinStatus.IDLE
    cycle: int = 0
    seq: int = 0
    wall_ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    sensors: MappingProxyType[str, float] = field(default_factory=lambda: MappingProxyType({}))
    op_settings: tuple[float, float, float] = (0.0, 0.0, 0.0)
    regime: int = 0

    health_index: float = 100.0
    band_tracker: BandTracker = field(default_factory=BandTracker)
    components: MappingProxyType[EngineModule, ComponentState] = field(
        default_factory=lambda: MappingProxyType({})
    )
    degradation_rate: float = 0.0

    prediction: Prediction | None = None
    anomaly_score: float = 0.0

    time_in_band: MappingProxyType[HealthBand, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def engine_id(self) -> EngineId:
        return self.spec.engine_id

    @property
    def health_band(self) -> HealthBand:
        return self.band_tracker.current

    @property
    def is_active(self) -> bool:
        return self.status is TwinStatus.RUNNING

    @property
    def component_scores(self) -> dict[EngineModule, float]:
        return {module: state.score for module, state in self.components.items()}

    @property
    def progress(self) -> float:
        """Fraction of the known trajectory consumed, in [0, 1]."""
        if self.spec.total_cycles <= 0:
            return 0.0
        return min(1.0, self.cycle / self.spec.total_cycles)


# ── Transition results ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of applying an input to a twin: new state plus emitted events."""

    state: TwinState
    events: tuple[DomainEvent, ...] = ()

    def with_event(self, event: DomainEvent) -> TransitionResult:
        return TransitionResult(self.state, (*self.events, event))


def _emit(
    state: TwinState,
    event_type: EventType,
    payload: dict[str, object],
    severity: Severity = Severity.INFO,
) -> tuple[TwinState, DomainEvent]:
    """Bump the sequence counter and build an event bound to the new sequence."""
    new_seq = state.seq + 1
    event = DomainEvent(
        engine_id=state.engine_id,
        seq=new_seq,
        cycle=state.cycle,
        event_type=event_type,
        severity=severity,
        payload=payload,
        ts=state.wall_ts,
    )
    return replace(state, seq=new_seq), event


def apply_command(
    state: TwinState,
    command: CommandType,
    args: dict[str, object] | None = None,
) -> TransitionResult:
    """Apply a control command, honouring the lifecycle FSM.

    Illegal transitions are never exceptions: they emit
    ``twin.command.rejected`` and leave the state otherwise unchanged.
    """
    args = args or {}

    if not can_apply(state.status, command):
        new_state, event = _emit(
            state,
            EventType.COMMAND_REJECTED,
            {
                "command": command.value,
                "reason": rejection_reason(state.status, command),
                "status": state.status.value,
            },
            Severity.LOW,
        )
        return TransitionResult(new_state, (event,))

    target = next_status(state.status, command)
    assert target is not None  # guaranteed by can_apply

    new_state = state
    events: list[DomainEvent] = []

    if command is CommandType.SEEK:
        raw_cycle = args.get("cycle", state.cycle)
        requested = raw_cycle if isinstance(raw_cycle, int) else state.cycle
        clamped = max(0, min(requested, state.spec.total_cycles))
        new_state = replace(new_state, cycle=clamped)
        new_state, event = _emit(
            new_state, EventType.CYCLE_ADVANCED, {"cycle": clamped, "seek": True}
        )
        events.append(event)

    elif command is CommandType.RESET:
        new_state = TwinState(spec=state.spec, seq=state.seq)
        new_state, event = _emit(new_state, EventType.RESET, {})
        events.append(event)
        return TransitionResult(new_state, tuple(events))

    if new_state.status is not target:
        new_state = replace(new_state, status=target)
        event_type = _STATUS_EVENT.get(target, EventType.STATUS_CHANGED)
        new_state, event = _emit(
            new_state,
            event_type,
            {"from": state.status.value, "to": target.value, "command": command.value},
        )
        events.append(event)

    return TransitionResult(new_state, tuple(events))


_STATUS_EVENT: MappingProxyType[TwinStatus, EventType] = MappingProxyType(
    {
        TwinStatus.RUNNING: EventType.STARTED,
        TwinStatus.PAUSED: EventType.PAUSED,
        TwinStatus.MAINTENANCE: EventType.MAINTENANCE_STARTED,
        TwinStatus.RETIRED: EventType.RETIRED,
    }
)


def apply_health_update(
    state: TwinState,
    *,
    components: dict[EngineModule, ComponentState],
    anomaly_score: float,
    prediction: Prediction | None,
    maintenance_applied: bool = False,
    model_trusted: bool = True,
) -> TransitionResult:
    """Recompute the health index and band, emitting change events.

    This is the fusion step of the tick loop (Doc 08 section 8.6). It is separated
    from telemetry application so it can be unit-tested against hand-built inputs.
    """
    component_scores = {module: comp.score for module, comp in components.items()}
    hi = fuse_health_index(
        HealthInputs(
            component_scores=component_scores,
            rul_p50=prediction.rul_p50 if prediction else None,
            anomaly_score=anomaly_score,
            previous_hi=state.health_index,
            maintenance_applied=maintenance_applied,
            model_trusted=model_trusted,
        )
    )

    tracker, band_changed = state.band_tracker.observe(hi)
    previous_band = state.band_tracker.current

    counts = dict(state.time_in_band)
    counts[tracker.current] = counts.get(tracker.current, 0) + 1

    rate = hi - state.health_index

    new_state = replace(
        state,
        health_index=hi,
        band_tracker=tracker,
        components=MappingProxyType(dict(components)),
        anomaly_score=anomaly_score,
        prediction=prediction if prediction is not None else state.prediction,
        degradation_rate=rate,
        time_in_band=MappingProxyType(counts),
    )

    events: list[DomainEvent] = []
    if band_changed:
        severity = (
            Severity.CRITICAL
            if tracker.current is HealthBand.CRITICAL
            else Severity.HIGH
            if tracker.current is HealthBand.WARNING
            else Severity.INFO
        )
        new_state, event = _emit(
            new_state,
            EventType.HEALTH_BAND_CHANGED,
            {
                "from": previous_band.value,
                "to": tracker.current.value,
                "health_index": round(hi, 2),
                "worst_module": _worst_module(component_scores),
            },
            severity,
        )
        events.append(event)

    return TransitionResult(new_state, tuple(events))


def _worst_module(scores: dict[EngineModule, float]) -> str | None:
    if not scores:
        return None
    worst: EngineModule = min(scores.items(), key=lambda item: item[1])[0]
    return str(worst.value)


def apply_telemetry(
    state: TwinState,
    *,
    cycle: int,
    sensors: dict[str, float],
    op_settings: tuple[float, float, float],
    regime: int,
    wall_ts: datetime | None = None,
) -> TransitionResult:
    """Advance the twin by one telemetry row.

    Emits ``twin.regime.changed`` when the operating regime switches and
    ``twin.failed`` when the trajectory reaches its end of life.
    """
    if not state.is_active:
        return TransitionResult(state)

    new_state = replace(
        state,
        cycle=cycle,
        sensors=MappingProxyType(dict(sensors)),
        op_settings=op_settings,
        regime=regime,
        wall_ts=wall_ts or datetime.now(UTC),
    )

    events: list[DomainEvent] = []

    if regime != state.regime:
        new_state, event = _emit(
            new_state, EventType.REGIME_CHANGED, {"from": state.regime, "to": regime}
        )
        events.append(event)

    if cycle >= state.spec.total_cycles > 0:
        new_state = replace(new_state, status=TwinStatus.FAILED)
        new_state, event = _emit(
            new_state,
            EventType.FAILED,
            {
                "cycle": cycle,
                "final_health_index": round(new_state.health_index, 2),
                "cause": "end_of_trajectory",
            },
            Severity.CRITICAL,
        )
        events.append(event)

    return TransitionResult(new_state, tuple(events))
