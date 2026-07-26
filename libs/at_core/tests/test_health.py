"""Unit and property tests for the health fusion model (Doc 08 section 8.6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from at_core.domain.enums import EngineModule, HealthBand
from at_core.domain.health import (
    BAND_LATCH_CYCLES,
    HI_MAX_RECOVERY_PER_CYCLE,
    W_ANOMALY,
    W_MODEL,
    W_PHYSICS,
    W_WORST_COMPONENT,
    BandTracker,
    ComponentState,
    HealthInputs,
    band_for,
    failure_probability,
    fuse_health_index,
    physics_term,
)

scores = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)


def test_fusion_weights_sum_to_one() -> None:
    total = W_PHYSICS + W_MODEL + W_ANOMALY + W_WORST_COMPONENT
    assert total == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("hi", "expected"),
    [
        (100.0, HealthBand.HEALTHY),
        (80.0, HealthBand.HEALTHY),
        (79.9, HealthBand.WATCH),
        (60.0, HealthBand.WATCH),
        (59.9, HealthBand.WARNING),
        (35.0, HealthBand.WARNING),
        (34.9, HealthBand.CRITICAL),
        (0.0, HealthBand.CRITICAL),
    ],
)
def test_band_thresholds_are_exact(hi: float, expected: HealthBand) -> None:
    assert band_for(hi) is expected


def test_band_ordering() -> None:
    assert HealthBand.HEALTHY < HealthBand.WATCH < HealthBand.WARNING < HealthBand.CRITICAL


@given(hi=st.floats(min_value=0.0, max_value=100.0))
def test_fused_index_always_in_range(hi: float) -> None:
    result = fuse_health_index(
        HealthInputs(
            component_scores={EngineModule.HPC: hi},
            rul_p50=hi,
            anomaly_score=hi / 25.0,
            previous_hi=None,
        )
    )
    assert 0.0 <= result <= 100.0


def test_perfect_health_yields_100() -> None:
    result = fuse_health_index(
        HealthInputs(
            component_scores=dict.fromkeys(EngineModule, 100.0),
            rul_p50=125.0,
            anomaly_score=0.0,
        )
    )
    assert result == pytest.approx(100.0)


def test_total_degradation_yields_zero() -> None:
    result = fuse_health_index(
        HealthInputs(
            component_scores=dict.fromkeys(EngineModule, 0.0),
            rul_p50=0.0,
            anomaly_score=10.0,
        )
    )
    assert result == pytest.approx(0.0)


def test_monotonic_decay_constraint_blocks_spontaneous_recovery() -> None:
    """Health must not jump upward without a maintenance action (Doc 08 section 8.6)."""
    previous = 40.0
    result = fuse_health_index(
        HealthInputs(
            component_scores=dict.fromkeys(EngineModule, 100.0),
            rul_p50=125.0,
            anomaly_score=0.0,
            previous_hi=previous,
            maintenance_applied=False,
        )
    )
    assert result <= previous + HI_MAX_RECOVERY_PER_CYCLE + 1e-9


def test_maintenance_releases_the_recovery_ceiling() -> None:
    previous = 40.0
    result = fuse_health_index(
        HealthInputs(
            component_scores=dict.fromkeys(EngineModule, 100.0),
            rul_p50=125.0,
            anomaly_score=0.0,
            previous_hi=previous,
            maintenance_applied=True,
        )
    )
    assert result > previous + HI_MAX_RECOVERY_PER_CYCLE


@given(
    previous=st.floats(min_value=0.0, max_value=100.0),
    component=scores,
)
def test_decay_constraint_holds_for_any_input(previous: float, component: float) -> None:
    result = fuse_health_index(
        HealthInputs(
            component_scores={EngineModule.HPC: component, EngineModule.HPT: component},
            rul_p50=component,
            anomaly_score=0.0,
            previous_hi=previous,
        )
    )
    assert result <= previous + HI_MAX_RECOVERY_PER_CYCLE + 1e-9


def test_physics_term_is_criticality_weighted_not_arithmetic_mean() -> None:
    """A degraded HPT (weight .22) must hurt more than a degraded NOZZLE (weight .05)."""
    hpt_bad = physics_term({EngineModule.HPT: 0.0, EngineModule.NOZZLE: 100.0})
    nozzle_bad = physics_term({EngineModule.HPT: 100.0, EngineModule.NOZZLE: 0.0})
    assert hpt_bad < nozzle_bad


def test_physics_term_empty_is_neutral() -> None:
    assert physics_term({}) == 1.0


# ── Band hysteresis ──────────────────────────────────────────────────────────


def test_band_latches_only_after_consecutive_observations() -> None:
    tracker = BandTracker()
    changed_at: int | None = None
    for i in range(BAND_LATCH_CYCLES):
        tracker, changed = tracker.observe(50.0)  # WARNING territory
        if changed:
            changed_at = i
    assert changed_at == BAND_LATCH_CYCLES - 1
    assert tracker.current is HealthBand.WARNING


def test_oscillation_does_not_flip_the_band() -> None:
    """Values straddling a threshold must not produce a band change (anti-flicker)."""
    tracker = BandTracker()
    flips = 0
    for value in [79.0, 81.0] * 10:
        tracker, changed = tracker.observe(value)
        flips += int(changed)
    assert flips == 0
    assert tracker.current is HealthBand.HEALTHY


def test_sustained_degradation_does_change_band() -> None:
    tracker = BandTracker()
    changes = 0
    for value in [79.0] * 5:
        tracker, changed = tracker.observe(value)
        changes += int(changed)
    assert changes == 1
    assert tracker.current is HealthBand.WATCH


# ── Failure probability ──────────────────────────────────────────────────────


@given(
    rul=st.floats(min_value=1.0, max_value=200.0),
    horizon=st.sampled_from([30, 60, 90]),
)
def test_failure_probability_in_unit_interval(rul: float, horizon: int) -> None:
    assert 0.0 <= failure_probability(rul, rul * 1.3, horizon) <= 1.0


def test_failure_probability_increases_with_horizon() -> None:
    p30 = failure_probability(50.0, 65.0, 30)
    p60 = failure_probability(50.0, 65.0, 60)
    p90 = failure_probability(50.0, 65.0, 90)
    assert p30 < p60 < p90


def test_failure_probability_higher_for_sicker_engine() -> None:
    healthy = failure_probability(120.0, 150.0, 30)
    sick = failure_probability(10.0, 15.0, 30)
    assert sick > healthy


# ── ComponentState validation ────────────────────────────────────────────────


def test_component_state_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError, match="out of range"):
        ComponentState(module=EngineModule.HPC, score=101.0)
