"""Tests closing the remaining branches in at_core.

The M1 exit criteria require 100 percent coverage of the domain kernel
(Doc 05 section 5.9), because every branch here is load-bearing business logic.
"""

from __future__ import annotations

import uuid

import pytest

from at_core.domain.enums import (
    CommandType,
    EngineModule,
    HealthBand,
    ReplaySpeed,
    Subset,
    TwinStatus,
)
from at_core.domain.health import ComponentState, physics_term
from at_core.domain.twin import (
    EngineSpec,
    TransitionResult,
    TwinState,
    apply_command,
)
from at_core.events import DomainEvent, EventType


def spec(total_cycles: int = 200) -> EngineSpec:
    return EngineSpec(
        engine_id=uuid.UUID(int=1),
        unit_number=1,
        subset=Subset.FD001,
        split="train",
        total_cycles=total_cycles,
    )


# ── enums ────────────────────────────────────────────────────────────────────


def test_health_band_comparison_with_foreign_type_is_not_implemented() -> None:
    """Guards the NotImplemented branch so Python can fall back correctly."""
    assert HealthBand.HEALTHY.__lt__("HEALTHY") is NotImplemented
    with pytest.raises(TypeError):
        _ = HealthBand.HEALTHY < 3  # type: ignore[operator]


def test_replay_speed_multiplier_parses_every_member() -> None:
    assert ReplaySpeed.X0_5.multiplier == 0.5
    assert ReplaySpeed.X32.multiplier == 32.0
    assert all(speed.multiplier > 0 for speed in ReplaySpeed)


# ── health ───────────────────────────────────────────────────────────────────


def test_physics_term_with_only_untracked_modules_is_neutral() -> None:
    """BEARINGS/CONTROL carry no criticality weight, so they cannot drive health."""
    assert physics_term({EngineModule.BEARINGS: 0.0, EngineModule.CONTROL: 0.0}) == 1.0


# ── twin properties ──────────────────────────────────────────────────────────


def test_component_scores_projection() -> None:
    from dataclasses import replace as dc_replace
    from types import MappingProxyType

    state = dc_replace(
        TwinState(spec=spec()),
        components=MappingProxyType({EngineModule.HPC: ComponentState(EngineModule.HPC, 42.0)}),
    )
    assert state.component_scores == {EngineModule.HPC: 42.0}


def test_progress_is_zero_when_trajectory_length_unknown() -> None:
    assert TwinState(spec=spec(total_cycles=0)).progress == 0.0


def test_transition_result_with_event_appends() -> None:
    state = TwinState(spec=spec())
    event = DomainEvent(
        engine_id=state.engine_id,
        seq=1,
        cycle=0,
        event_type=EventType.PROVISIONED,
        payload={},
    )
    result = TransitionResult(state).with_event(event)
    assert result.events == (event,)


# ── SEEK command ─────────────────────────────────────────────────────────────


def test_seek_clamps_beyond_end_of_trajectory() -> None:
    state = apply_command(TwinState(spec=spec(100)), CommandType.START).state
    result = apply_command(state, CommandType.SEEK, {"cycle": 500})
    assert result.state.cycle == 100
    assert result.events[0].payload["seek"] is True


def test_seek_clamps_negative_to_zero() -> None:
    state = apply_command(TwinState(spec=spec(100)), CommandType.START).state
    result = apply_command(state, CommandType.SEEK, {"cycle": -20})
    assert result.state.cycle == 0


def test_seek_without_argument_holds_position() -> None:
    state = apply_command(TwinState(spec=spec(100)), CommandType.START).state
    result = apply_command(state, CommandType.SEEK)
    assert result.state.cycle == state.cycle


def test_seek_preserves_running_status() -> None:
    state = apply_command(TwinState(spec=spec(100)), CommandType.START).state
    result = apply_command(state, CommandType.SEEK, {"cycle": 10})
    assert result.state.status is TwinStatus.RUNNING


def test_set_speed_is_a_legal_self_transition() -> None:
    state = apply_command(TwinState(spec=spec()), CommandType.START).state
    result = apply_command(state, CommandType.SET_SPEED, {"speed": 8})
    assert result.state.status is TwinStatus.RUNNING


def test_retire_from_running() -> None:
    state = apply_command(TwinState(spec=spec()), CommandType.START).state
    result = apply_command(state, CommandType.RETIRE)
    assert result.state.status is TwinStatus.RETIRED
    assert result.events[-1].event_type is EventType.RETIRED


def test_maintenance_transition_emits_maintenance_started() -> None:
    state = apply_command(TwinState(spec=spec()), CommandType.START).state
    result = apply_command(state, CommandType.PERFORM_MAINTENANCE, {"module": "HPC"})
    assert result.state.status is TwinStatus.MAINTENANCE
    assert result.events[-1].event_type is EventType.MAINTENANCE_STARTED


# ── event serialisation ──────────────────────────────────────────────────────


def test_event_to_dict_is_json_ready() -> None:
    import json

    event = DomainEvent(
        engine_id=uuid.UUID(int=9),
        seq=3,
        cycle=17,
        event_type=EventType.HEALTH_BAND_CHANGED,
        payload={"from": "WATCH", "to": "WARNING"},
        trace_id="trace-abc",
    )
    payload = event.to_dict()
    assert json.loads(json.dumps(payload))["event_type"] == "twin.health.band_changed"
    assert payload["trace_id"] == "trace-abc"
    assert payload["engine_id"] == str(uuid.UUID(int=9))


def test_band_change_with_no_component_data_reports_no_worst_module() -> None:
    """Before the physics kernel populates components, banding must still work."""
    from at_core.domain.twin import Prediction, apply_health_update

    state = apply_command(TwinState(spec=spec()), CommandType.START).state
    for _ in range(30):
        result = apply_health_update(
            state,
            components={},
            anomaly_score=4.0,
            prediction=Prediction(rul_p50=0.0),
        )
        state = result.state
        for event in result.events:
            if event.event_type is EventType.HEALTH_BAND_CHANGED:
                assert event.payload["worst_module"] is None
                return
    pytest.fail("expected a band change with empty component data")
