"""Tests for the twin aggregate, FSM and event emission."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from at_core.domain.enums import (
    CommandType,
    EngineModule,
    HealthBand,
    Subset,
    TwinStatus,
)
from at_core.domain.fsm import TRANSITIONS, can_apply, next_status, rejection_reason
from at_core.domain.health import BandTracker, ComponentState
from at_core.domain.twin import (
    EngineSpec,
    Prediction,
    TwinState,
    apply_command,
    apply_health_update,
    apply_telemetry,
)
from at_core.events import EventType


def make_spec(total_cycles: int = 200) -> EngineSpec:
    return EngineSpec(
        engine_id=uuid.uuid4(),
        unit_number=27,
        subset=Subset.FD001,
        split="train",
        total_cycles=total_cycles,
    )


def running_twin(total_cycles: int = 200) -> TwinState:
    state = TwinState(spec=make_spec(total_cycles))
    return apply_command(state, CommandType.START).state


# ── Identity ─────────────────────────────────────────────────────────────────


def test_external_ref_format() -> None:
    assert make_spec().external_ref == "FD001-train-U27"


def test_subset_properties_match_dataset_facts() -> None:
    assert Subset.FD001.n_conditions == 1
    assert Subset.FD002.n_conditions == 6
    assert Subset.FD003.n_fault_modes == 2
    assert Subset.FD002.window_size == 20
    assert Subset.FD001.window_size == 30


# ── FSM ──────────────────────────────────────────────────────────────────────


def test_start_transitions_idle_to_running() -> None:
    state = TwinState(spec=make_spec())
    result = apply_command(state, CommandType.START)
    assert result.state.status is TwinStatus.RUNNING
    assert result.events[0].event_type is EventType.STARTED


def test_illegal_command_is_rejected_not_raised() -> None:
    """Invalid transitions must emit an event, never raise (Doc 08 section 8.3)."""
    state = TwinState(spec=make_spec())  # IDLE
    result = apply_command(state, CommandType.PAUSE)
    assert result.state.status is TwinStatus.IDLE
    assert len(result.events) == 1
    assert result.events[0].event_type is EventType.COMMAND_REJECTED
    assert "not valid" in result.events[0].payload["reason"]


def test_rejection_reason_lists_legal_commands() -> None:
    reason = rejection_reason(TwinStatus.IDLE, CommandType.PAUSE)
    assert "START" in reason


def test_terminal_status_rejection_message() -> None:
    reason = rejection_reason(TwinStatus.RETIRED, CommandType.START)
    assert "RETIRED" in reason


@pytest.mark.parametrize(("status", "command"), list(TRANSITIONS.keys()))
def test_every_declared_transition_is_applicable(status: TwinStatus, command: CommandType) -> None:
    assert can_apply(status, command)
    assert next_status(status, command) is not None


def test_pause_resume_round_trip() -> None:
    state = running_twin()
    paused = apply_command(state, CommandType.PAUSE).state
    assert paused.status is TwinStatus.PAUSED
    resumed = apply_command(paused, CommandType.RESUME).state
    assert resumed.status is TwinStatus.RUNNING


def test_reset_clears_state_but_preserves_sequence() -> None:
    state = running_twin()
    state = apply_telemetry(
        state, cycle=50, sensors={"s3": 1590.0}, op_settings=(0, 0, 100), regime=0
    ).state
    paused = apply_command(state, CommandType.PAUSE).state
    reset = apply_command(paused, CommandType.RESET).state
    assert reset.status is TwinStatus.IDLE
    assert reset.cycle == 0
    assert reset.seq > paused.seq, "sequence must keep growing for event sourcing"


# ── Sequence monotonicity (event sourcing invariant) ─────────────────────────


def test_sequence_is_strictly_monotonic_across_mixed_operations() -> None:
    state = running_twin()
    seqs: list[int] = []
    for cycle in range(1, 20):
        state = apply_telemetry(
            state,
            cycle=cycle,
            sensors={"s3": 1590.0 + cycle},
            op_settings=(0.0, 0.0, 100.0),
            regime=0,
        ).state
        result = apply_health_update(
            state,
            components={EngineModule.HPC: ComponentState(EngineModule.HPC, 100.0 - cycle * 3)},
            anomaly_score=0.0,
            prediction=Prediction(rul_p50=120.0 - cycle),
        )
        state = result.state
        seqs.extend(event.seq for event in result.events)
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs)), "sequence numbers must be unique"


def test_events_carry_the_engine_id() -> None:
    state = TwinState(spec=make_spec())
    result = apply_command(state, CommandType.START)
    assert result.events[0].engine_id == state.engine_id


# ── Telemetry ────────────────────────────────────────────────────────────────


def test_telemetry_ignored_when_not_running() -> None:
    state = TwinState(spec=make_spec())  # IDLE
    result = apply_telemetry(state, cycle=5, sensors={"s3": 1.0}, op_settings=(0, 0, 0), regime=0)
    assert result.state.cycle == 0
    assert result.events == ()


def test_regime_change_emits_event() -> None:
    state = running_twin()
    result = apply_telemetry(
        state, cycle=1, sensors={"s3": 1.0}, op_settings=(35.0, 0.84, 100.0), regime=3
    )
    types = [event.event_type for event in result.events]
    assert EventType.REGIME_CHANGED in types


def test_end_of_trajectory_fails_the_twin() -> None:
    state = running_twin(total_cycles=10)
    result = apply_telemetry(state, cycle=10, sensors={"s3": 1.0}, op_settings=(0, 0, 0), regime=0)
    assert result.state.status is TwinStatus.FAILED
    assert result.events[-1].event_type is EventType.FAILED
    assert result.events[-1].payload["cause"] == "end_of_trajectory"


def test_progress_fraction() -> None:
    state = running_twin(total_cycles=200)
    state = apply_telemetry(state, cycle=50, sensors={}, op_settings=(0, 0, 0), regime=0).state
    assert state.progress == pytest.approx(0.25)


# ── Health update integration ────────────────────────────────────────────────


def test_health_band_change_emits_event_with_worst_module() -> None:
    state = replace(running_twin(), health_index=40.0)
    components = {
        EngineModule.HPC: ComponentState(EngineModule.HPC, 20.0),
        EngineModule.FAN: ComponentState(EngineModule.FAN, 90.0),
    }
    for _ in range(20):
        result = apply_health_update(
            state,
            components=components,
            anomaly_score=3.0,
            prediction=Prediction(rul_p50=10.0),
        )
        state = result.state
        for event in result.events:
            if event.event_type is EventType.HEALTH_BAND_CHANGED:
                assert event.payload["worst_module"] == "HPC"
                return
    pytest.fail("expected a band change event during sustained degradation")


def test_health_update_records_time_in_band() -> None:
    state = running_twin()
    for _ in range(4):
        state = apply_health_update(
            state,
            components={EngineModule.HPC: ComponentState(EngineModule.HPC, 95.0)},
            anomaly_score=0.0,
            prediction=Prediction(rul_p50=125.0),
        ).state
    assert state.time_in_band[HealthBand.HEALTHY] == 4


def test_prediction_is_retained_when_update_has_none() -> None:
    """A missed inference must not wipe the last-good prediction (Doc 05 section 5.5)."""
    state = running_twin()
    state = apply_health_update(
        state,
        components={},
        anomaly_score=0.0,
        prediction=Prediction(rul_p50=88.0, model_id="m1"),
    ).state
    state = apply_health_update(state, components={}, anomaly_score=0.0, prediction=None).state
    assert state.prediction is not None
    assert state.prediction.rul_p50 == 88.0


# ── Immutability ─────────────────────────────────────────────────────────────


def test_state_is_immutable() -> None:
    state = running_twin()
    with pytest.raises((AttributeError, TypeError)):
        state.cycle = 99  # type: ignore[misc]


def test_transitions_do_not_mutate_the_input_state() -> None:
    state = running_twin()
    before_cycle, before_seq = state.cycle, state.seq
    apply_telemetry(state, cycle=42, sensors={"s3": 1.0}, op_settings=(0, 0, 0), regime=1)
    assert state.cycle == before_cycle
    assert state.seq == before_seq


# ── Determinism (P2 / Doc 08 section 8.8) ────────────────────────────────────


@given(cycles=st.integers(min_value=1, max_value=40))
def test_replay_is_deterministic(cycles: int) -> None:
    """Identical inputs must produce identical state -- required for event sourcing."""

    def run() -> tuple[float, int, str]:
        spec = EngineSpec(
            engine_id=uuid.UUID(int=7),
            unit_number=1,
            subset=Subset.FD001,
            split="train",
            total_cycles=500,
        )
        state = apply_command(TwinState(spec=spec), CommandType.START).state
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        for cycle in range(1, cycles + 1):
            state = apply_telemetry(
                state,
                cycle=cycle,
                sensors={"s3": 1580.0 + cycle * 0.7},
                op_settings=(0.0, 0.0, 100.0),
                regime=0,
                wall_ts=ts,
            ).state
            state = apply_health_update(
                state,
                components={
                    EngineModule.HPC: ComponentState(
                        EngineModule.HPC, max(0.0, 100.0 - cycle * 1.5)
                    )
                },
                anomaly_score=min(4.0, cycle * 0.05),
                prediction=Prediction(rul_p50=max(0.0, 125.0 - cycle)),
            ).state
        return round(state.health_index, 9), state.seq, state.health_band.value

    assert run() == run()


def test_band_tracker_default_is_healthy() -> None:
    assert BandTracker().current is HealthBand.HEALTHY
