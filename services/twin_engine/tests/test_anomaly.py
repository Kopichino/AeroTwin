"""Tests for anomaly detection (Doc 07 section 7.6).

The ground-truth tests matter most: FD001 and FD003 degrade the HPC, so a working
detector must attribute the anomaly to the HPC on essentially every unit, alert
well before end of life, and stay quiet while the engine is healthy.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from at_core.domain.enums import EngineModule, Severity, Subset
from at_twin.anomaly import (
    CONFIRM_CYCLES,
    CUSUM_DECAY,
    MIN_OBSERVATIONS,
    SIGMA_CRITICAL,
    SIGMA_LOW,
    DetectorState,
    SensorStats,
    detect,
    severity_for,
)

INTERIM = Path("data/interim")
dataset = pytest.mark.skipif(
    not (INTERIM / "FD001_train.parquet").is_file(),
    reason="interim parquet not present; run `make data`",
)

NOMINAL = {"s3": 1586.0, "s4": 1400.0, "s11": 47.2, "s20": 39.0}


def warm(state: DetectorState, cycles: int = 40, **overrides: float) -> DetectorState:
    """Feed healthy cycles with slight noise so sigma is well estimated."""
    rng = np.random.default_rng(0)
    sensors = {**NOMINAL, **overrides}
    for _ in range(cycles):
        noisy = {k: v + float(rng.normal(0, abs(v) * 0.0005)) for k, v in sensors.items()}
        state, _ = detect(state, noisy, 0, learning=True)
    return state


# ── running statistics ───────────────────────────────────────────────────────


def test_welford_matches_numpy() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    stats = SensorStats()
    for value in values:
        stats = stats.observe(value)
    assert stats.mean == pytest.approx(float(np.mean(values)))
    assert stats.std == pytest.approx(float(np.std(values, ddof=1)))


def test_zscore_is_suppressed_until_enough_observations() -> None:
    stats = SensorStats()
    for _ in range(MIN_OBSERVATIONS - 1):
        stats = stats.observe(10.0)
    assert stats.zscore(500.0) == 0.0


def test_constant_channel_reports_a_bounded_deviation() -> None:
    """A perfectly constant sensor cannot express sigma, but movement matters."""
    stats = SensorStats()
    for _ in range(20):
        stats = stats.observe(5.0)
    assert stats.zscore(5.0) == 0.0
    assert stats.zscore(7.0) > 0.0


def test_stats_are_immutable() -> None:
    stats = SensorStats()
    stats.observe(1.0)
    assert stats.count == 0


# ── severity banding ─────────────────────────────────────────────────────────


def test_severity_bands_are_ordered() -> None:
    assert severity_for(0.0) is Severity.INFO
    assert severity_for(SIGMA_LOW) is Severity.LOW
    assert severity_for(SIGMA_CRITICAL) is Severity.CRITICAL


def test_thresholds_account_for_multiple_comparisons() -> None:
    """A 2-sigma trigger fires constantly across 3 detectors x 21 sensors.

    Measured at a 57-80 % false-positive rate on healthy C-MAPSS trajectories,
    which is why the bands sit well above the textbook single-channel value.
    """
    assert SIGMA_LOW >= 4.0


# ── detection behaviour ──────────────────────────────────────────────────────


def test_healthy_engine_does_not_alert() -> None:
    state = warm(DetectorState())
    for _ in range(30):
        state, reading = detect(state, NOMINAL, 0)
        assert not reading.is_alerting


def test_sustained_shift_raises_an_alert() -> None:
    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1620.0, "s4": 1450.0}
    alerted = False
    for _ in range(30):
        state, reading = detect(state, faulted, 0)
        alerted = alerted or reading.is_alerting
    assert alerted


def test_single_spike_does_not_alert() -> None:
    """One excursion is noise; an engineer cares about persistence."""
    state = warm(DetectorState())
    state, reading = detect(state, {**NOMINAL, "s3": 1700.0}, 0)
    assert not reading.is_alerting


def test_confirmation_requires_consecutive_cycles() -> None:
    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1650.0, "s4": 1480.0, "s11": 49.5}

    first_alert = None
    for cycle in range(1, 20):
        state, reading = detect(state, faulted, 0)
        if reading.is_alerting and first_alert is None:
            first_alert = cycle
    assert first_alert is not None
    assert first_alert >= CONFIRM_CYCLES


def test_alert_reports_contributing_sensors_and_module() -> None:
    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1650.0, "s11": 49.5}
    for _ in range(12):
        state, reading = detect(state, faulted, 0)
    assert reading.sensors
    assert reading.module is EngineModule.HPC


def test_anomaly_resolves_after_sustained_quiet() -> None:
    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1660.0, "s4": 1490.0}
    for _ in range(15):
        state, _ = detect(state, faulted, 0)
    assert state.active

    resolved = False
    for _ in range(40):
        state, reading = detect(state, NOMINAL, 0)
        resolved = resolved or reading.is_resolved
    assert resolved


def test_learning_stops_the_baseline_from_absorbing_a_fault() -> None:
    """Adaptive thresholds that keep learning during a fault mask it entirely."""
    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1650.0}

    frozen = state
    for _ in range(30):
        frozen, reading_frozen = detect(frozen, faulted, 0, learning=False)

    adapting = state
    for _ in range(30):
        adapting, reading_adapting = detect(adapting, faulted, 0, learning=True)

    assert reading_frozen.score > reading_adapting.score


def test_cusum_decays_rather_than_saturating() -> None:
    """Without decay CUSUM ran to 291 sigma and reported CRITICAL forever."""
    assert 0.0 < CUSUM_DECAY < 1.0

    state = warm(DetectorState())
    faulted = {**NOMINAL, "s3": 1650.0}
    for _ in range(40):
        state, _ = detect(state, faulted, 0)
    peak = max([*state.cusum_high.values(), *state.cusum_low.values()])

    for _ in range(60):
        state, _ = detect(state, NOMINAL, 0)
    after = max([*state.cusum_high.values(), *state.cusum_low.values()])
    assert after < peak


def test_detector_state_is_immutable() -> None:
    state = warm(DetectorState())
    before = state.quiet_cycles
    detect(state, NOMINAL, 0)
    assert state.quiet_cycles == before


def test_regimes_are_tracked_separately() -> None:
    """A value normal for cruise must not look anomalous when flown at altitude."""
    state = DetectorState()
    for _ in range(30):
        state, _ = detect(state, {"s3": 1586.0}, 0, learning=True)
        state, _ = detect(state, {"s3": 1260.0}, 3, learning=True)

    _, reading_ground = detect(state, {"s3": 1586.0}, 0)
    _, reading_cruise = detect(state, {"s3": 1260.0}, 3)
    assert reading_ground.score < SIGMA_LOW
    assert reading_cruise.score < SIGMA_LOW


def test_empty_sensor_input_is_safe() -> None:
    _, reading = detect(DetectorState(), {}, 0)
    assert reading.score == 0.0
    assert reading.detector == "none"


# ── ground truth ─────────────────────────────────────────────────────────────


def _evaluate(subset: Subset, limit: int = 30) -> dict[str, float]:
    from at_data.parse import load_parquet

    frame = load_parquet(subset, "train", INTERIM)
    columns = [f"s{i}" for i in range(1, 22)]
    leads: list[int] = []
    false_positives: list[float] = []
    modules: Counter[str] = Counter()

    for unit in sorted(frame["unit_number"].unique())[:limit]:
        rows = frame[frame["unit_number"] == unit][columns].to_numpy()
        length = len(rows)
        state = DetectorState()
        first_alert: int | None = None
        healthy_alerts = 0
        healthy_window = int(length * 0.20)
        last_module: EngineModule | None = None

        for index in range(length):
            sensors = {f"s{i + 1}": float(rows[index, i]) for i in range(21)}
            state, reading = detect(state, sensors, 0, learning=(index < 20))
            if reading.is_alerting:
                if first_alert is None:
                    first_alert = index + 1
                if 20 <= index < healthy_window:
                    healthy_alerts += 1
                last_module = reading.module

        if first_alert is not None:
            leads.append(length - first_alert)
        if last_module is not None:
            modules[last_module.value] += 1
        false_positives.append(healthy_alerts / max(1, healthy_window - 20))

    return {
        "detection_rate": len(leads) / limit,
        "lead_median": float(np.median(leads)) if leads else 0.0,
        "false_positive_rate": float(np.mean(false_positives)),
        "hpc_share": modules["HPC"] / limit,
    }


@dataset
@pytest.mark.parametrize("subset", [Subset.FD001, Subset.FD003])
def test_detector_finds_every_failing_engine_with_useful_lead_time(
    subset: Subset,
) -> None:
    result = _evaluate(subset)
    assert result["detection_rate"] == 1.0
    assert result["lead_median"] > 50, f"lead time {result['lead_median']} too short"


@dataset
@pytest.mark.parametrize("subset", [Subset.FD001, Subset.FD003])
def test_false_positive_rate_on_healthy_engines_is_acceptable(subset: Subset) -> None:
    """Measured over the first 20 % of life, before meaningful degradation."""
    assert _evaluate(subset)["false_positive_rate"] < 0.15


@dataset
@pytest.mark.parametrize("subset", [Subset.FD001, Subset.FD003])
def test_anomaly_is_attributed_to_the_documented_fault_module(
    subset: Subset,
) -> None:
    """Both subsets degrade the HPC; attribution must agree with NASA."""
    assert _evaluate(subset)["hpc_share"] >= 0.8
