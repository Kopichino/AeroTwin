"""Tests for the physics-informed component health kernel (Doc 08 section 8.4).

The most important tests here are the ground-truth ones: FD001 and FD003 have a
documented HPC degradation fault mode, so a physics kernel that works must
identify the HPC as the dominant failing module on most units. That is a far
stronger check than asserting the arithmetic of a weighted sum.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from at_core.domain.enums import EngineModule, Subset
from at_core.domain.health import ComponentState
from at_core.domain.sensors import TRACKED_MODULES
from at_twin.physics import (
    BASELINE_CYCLES,
    MIN_CYCLES_FOR_HEALTH,
    PROXIES,
    BaselineAccumulator,
    apply_maintenance,
    compute_component_health,
    compute_proxies,
)

INTERIM = Path("data/interim")
dataset = pytest.mark.skipif(
    not (INTERIM / "FD001_train.parquet").is_file(),
    reason="interim parquet not present; run `make data`",
)

HEALTHY = {
    "s2": 642.0,
    "s3": 1586.0,
    "s4": 1400.0,
    "s7": 554.0,
    "s8": 2388.0,
    "s9": 9050.0,
    "s11": 47.2,
    "s12": 522.0,
    "s13": 2388.0,
    "s14": 8132.0,
    "s15": 8.41,
    "s17": 392.0,
    "s20": 39.0,
    "s21": 23.4,
}

#: The same engine at end of life. These are the *measured* final-cycle values for
#: FD001 unit 1 (see docs/reports/eda.md section 6), not invented numbers, so the
#: relative magnitudes across modules are physically faithful.
DEGRADED = {
    **HEALTHY,
    "s3": 1600.3,  # T30  +0.86 %
    "s4": 1425.9,  # T50  +1.79 %
    "s11": 48.16,  # Ps30 +1.89 %
    "s17": 395.8,  # htBleed +1.01 %
    "s20": 38.48,  # W31  -1.26 %
    "s21": 23.05,  # W32  -1.45 %
    "s9": 9039.8,  # Nc   -0.11 %
}


def settled_baseline(sensors: dict[str, float], regime: int = 0) -> BaselineAccumulator:
    """A fully established baseline from a steady healthy engine."""
    baseline = BaselineAccumulator()
    for _ in range(BASELINE_CYCLES):
        baseline = baseline.observe(compute_proxies(sensors), regime)
    return baseline


# ── proxy computation ────────────────────────────────────────────────────────


def test_proxy_catalogue_covers_every_tracked_module() -> None:
    covered = {spec.module for spec in PROXIES}
    assert covered == set(TRACKED_MODULES)


def test_hpc_temp_ratio_is_the_compressor_temperature_rise() -> None:
    proxies = compute_proxies({"s2": 640.0, "s3": 1600.0})
    assert proxies["hpc_temp_ratio"] == pytest.approx(2.5)


def test_missing_sensors_are_skipped_not_faked() -> None:
    proxies = compute_proxies({"s3": 1600.0})
    assert "hpc_temp_ratio" not in proxies  # needs s2 as well
    assert "hpt_outlet_temp" not in proxies


def test_non_finite_sensor_values_are_ignored() -> None:
    proxies = compute_proxies({"s2": float("nan"), "s3": 1600.0, "s4": 1400.0})
    assert "hpc_temp_ratio" not in proxies
    assert proxies["hpt_outlet_temp"] == 1400.0


def test_zero_divisor_does_not_raise() -> None:
    assert "hpc_temp_ratio" not in compute_proxies({"s2": 0.0, "s3": 1600.0})


# ── baseline accumulation ────────────────────────────────────────────────────


def test_baseline_is_not_ready_immediately() -> None:
    baseline = BaselineAccumulator().observe(compute_proxies(HEALTHY))
    assert not baseline.is_ready


def test_baseline_becomes_ready_then_complete() -> None:
    baseline = BaselineAccumulator()
    for index in range(1, BASELINE_CYCLES + 1):
        baseline = baseline.observe(compute_proxies(HEALTHY))
        assert baseline.is_ready == (index >= MIN_CYCLES_FOR_HEALTH)
    assert baseline.is_complete


def test_baselines_are_tracked_separately_per_regime() -> None:
    """The M3 bug: pooling regimes made the combustor look worst on every engine.

    In FD002 the six flight conditions move fuel ratio by a factor of four while a
    whole life of degradation moves it a few percent. A pooled baseline therefore
    measures the flight condition, not wear.
    """
    hot = {**HEALTHY, "s12": 522.0}
    cruise = {**HEALTHY, "s12": 165.0}

    baseline = BaselineAccumulator()
    for _ in range(BASELINE_CYCLES):
        baseline = baseline.observe(compute_proxies(hot), regime=0)
        baseline = baseline.observe(compute_proxies(cruise), regime=3)

    assert baseline.mean(0)["combustor_fuel_ratio"] == pytest.approx(522.0)
    assert baseline.mean(3)["combustor_fuel_ratio"] == pytest.approx(165.0)

    # Judged against its own regime, a cruise cycle is healthy; judged against the
    # pooled mean it would look catastrophically degraded.
    health = compute_component_health(cruise, baseline, regime=3)
    assert health[EngineModule.COMBUSTOR].score > 95.0


def test_unseen_regime_holds_the_previous_assessment() -> None:
    """Encountering a new flight condition must not reset health to nominal."""
    baseline = settled_baseline(HEALTHY)
    degraded = compute_component_health(DEGRADED, baseline, regime=0)
    unseen = compute_component_health(DEGRADED, baseline, previous=degraded, regime=5)
    assert unseen == degraded


def test_baseline_stops_accumulating_once_complete() -> None:
    """A late-life cycle must never contaminate the healthy reference."""
    baseline = settled_baseline(HEALTHY)
    contaminated = baseline.observe(compute_proxies(DEGRADED))
    assert contaminated.mean() == baseline.mean()
    assert contaminated.total_count == BASELINE_CYCLES


def test_baseline_is_immutable() -> None:
    original = BaselineAccumulator()
    original.observe(compute_proxies(HEALTHY))
    assert original.total_count == 0


def test_baseline_mean_of_empty_accumulator() -> None:
    assert BaselineAccumulator().mean() == {}


# ── health computation ───────────────────────────────────────────────────────


def test_all_modules_nominal_before_baseline_is_ready() -> None:
    """Guessing at health from an unestablished baseline would be dishonest."""
    health = compute_component_health(HEALTHY, BaselineAccumulator())
    assert set(health) == set(TRACKED_MODULES)
    assert all(state.score == 100.0 for state in health.values())


def test_healthy_engine_scores_near_nominal() -> None:
    health = compute_component_health(HEALTHY, settled_baseline(HEALTHY))
    for module, state in health.items():
        assert state.score > 95.0, f"{module} scored {state.score} on baseline data"


def test_degraded_engine_loses_health() -> None:
    baseline = settled_baseline(HEALTHY)
    health = compute_component_health(DEGRADED, baseline)
    assert health[EngineModule.HPC].score < 60.0


def test_degradation_concentrates_on_the_hot_section() -> None:
    """FD001 unit 1 at end of life must implicate the HPC and turbines.

    Deliberately asserted as a set rather than a single argmin: on one engine the
    HPC and LPT scores can sit within a point of each other, and a test that
    depends on that ordering is measuring noise. Fault-mode attribution is
    validated properly, fleet-wide, in
    ``test_kernel_recovers_the_documented_hpc_fault_mode``.
    """
    health = compute_component_health(DEGRADED, settled_baseline(HEALTHY))
    ranked = sorted(health.items(), key=lambda item: item[1].score)
    worst_three = {module for module, _ in ranked[:3]}
    assert EngineModule.HPC in worst_three
    assert worst_three <= {EngineModule.HPC, EngineModule.HPT, EngineModule.LPT}

    # Cold-section modules must remain healthy: nothing in this signature
    # implicates the fan, booster or combustor.
    assert health[EngineModule.FAN].score > 90.0
    assert health[EngineModule.LPC].score > 90.0


def test_drivers_name_the_responsible_proxies() -> None:
    health = compute_component_health(DEGRADED, settled_baseline(HEALTHY))
    drivers = health[EngineModule.HPC].drivers
    assert drivers, "a degraded module must explain itself"
    assert "hpc_temp_ratio" in drivers


def test_healthy_module_reports_no_drivers() -> None:
    health = compute_component_health(HEALTHY, settled_baseline(HEALTHY))
    assert health[EngineModule.HPC].drivers == ()


def test_scores_stay_within_range_under_extreme_input() -> None:
    absurd = {key: value * 3.0 for key, value in HEALTHY.items()}
    health = compute_component_health(absurd, settled_baseline(HEALTHY))
    for state in health.values():
        assert 0.0 <= state.score <= 100.0


def test_smoothing_damps_single_cycle_noise() -> None:
    """One noisy cycle must not move a module across a health band."""
    baseline = settled_baseline(HEALTHY)
    steady = compute_component_health(HEALTHY, baseline)
    spiked = compute_component_health(DEGRADED, baseline, previous=steady)
    unsmoothed = compute_component_health(DEGRADED, baseline)
    assert spiked[EngineModule.HPC].score > unsmoothed[EngineModule.HPC].score


def test_degradation_rate_is_reported() -> None:
    baseline = settled_baseline(HEALTHY)
    first = compute_component_health(HEALTHY, baseline)
    second = compute_component_health(DEGRADED, baseline, previous=first)
    assert second[EngineModule.HPC].degradation_rate < 0.0


def test_core_speed_droop_uses_raw_speed_not_the_corrected_ratio() -> None:
    """Regression guard for a real bug found during M3.

    Nc/NRc corrects for inlet conditions, which cancels the droop and inverts its
    sign: the ratio *rises* 0.11 % while raw Nc *falls* 0.11 %. Using the ratio
    reported an improving nozzle on a failing engine.
    """
    baseline = settled_baseline(HEALTHY)
    drooping = {**HEALTHY, "s9": 9040.0}
    health = compute_component_health(drooping, baseline)
    assert health[EngineModule.NOZZLE].score < 100.0


# ── maintenance ──────────────────────────────────────────────────────────────


def test_maintenance_restores_toward_nominal() -> None:
    components = {EngineModule.HPC: ComponentState(EngineModule.HPC, 40.0)}
    restored = apply_maintenance(components, EngineModule.HPC, 0.6, cycle=100)
    assert restored[EngineModule.HPC].score == pytest.approx(40.0 + 60.0 * 0.6)
    assert restored[EngineModule.HPC].last_maintained_cycle == 100


def test_overhaul_nearly_fully_restores() -> None:
    components = {EngineModule.HPC: ComponentState(EngineModule.HPC, 10.0)}
    restored = apply_maintenance(components, EngineModule.HPC, 0.98, cycle=5)
    assert restored[EngineModule.HPC].score > 98.0


def test_maintenance_clears_drivers_and_rate() -> None:
    components = {
        EngineModule.HPC: ComponentState(
            EngineModule.HPC, 40.0, degradation_rate=-2.0, drivers=("hpc_temp_ratio",)
        )
    }
    restored = apply_maintenance(components, EngineModule.HPC, 0.6, cycle=1)
    assert restored[EngineModule.HPC].drivers == ()
    assert restored[EngineModule.HPC].degradation_rate == 0.0


def test_maintenance_on_unknown_module_is_a_no_op() -> None:
    components = {EngineModule.HPC: ComponentState(EngineModule.HPC, 40.0)}
    assert apply_maintenance(components, EngineModule.FAN, 0.6, 1) == components


def test_maintenance_does_not_mutate_the_input() -> None:
    components = {EngineModule.HPC: ComponentState(EngineModule.HPC, 40.0)}
    apply_maintenance(components, EngineModule.HPC, 0.9, 1)
    assert components[EngineModule.HPC].score == 40.0


# ── ground truth: the kernel must recover the documented fault mode ──────────


def _worst_modules(subset: Subset, limit: int | None = None) -> Counter[str]:
    from at_data.parse import load_parquet

    frame = load_parquet(subset, "train", INTERIM)
    units = sorted(frame["unit_number"].unique())[:limit]
    sensor_columns = [f"s{i}" for i in range(1, 22)]
    tally: Counter[str] = Counter()

    for unit in units:
        rows = frame[frame["unit_number"] == unit][sensor_columns].to_numpy()
        baseline = BaselineAccumulator()
        previous: dict[EngineModule, ComponentState] | None = None
        for index in range(len(rows)):
            sensors = {f"s{i + 1}": float(rows[index, i]) for i in range(21)}
            if not baseline.is_complete:
                baseline = baseline.observe(compute_proxies(sensors))
            previous = compute_component_health(
                sensors, baseline, previous=previous, cycle=index + 1
            )
        assert previous is not None
        tally[min(previous.items(), key=lambda item: item[1].score)[0].value] += 1

    return tally


@dataset
@pytest.mark.parametrize(
    ("subset", "min_share"),
    [(Subset.FD001, 0.60), (Subset.FD003, 0.70)],
)
def test_kernel_recovers_the_documented_hpc_fault_mode(subset: Subset, min_share: float) -> None:
    """FD001 and FD003 both degrade the HPC. The kernel must agree.

    This is the single most important test of the physics model: it validates
    against NASA's documented fault mode, not against our own arithmetic.
    """
    tally = _worst_modules(subset, limit=40)
    total = sum(tally.values())
    share = tally["HPC"] / total
    assert share >= min_share, f"{subset.value}: HPC dominant on only {share:.0%} of units"


@dataset
@pytest.mark.parametrize(
    ("subset", "min_share"),
    [(Subset.FD002, 0.60), (Subset.FD004, 0.70)],
)
def test_multi_regime_subsets_also_recover_the_hpc_fault(subset: Subset, min_share: float) -> None:
    """The six-condition subsets, which is where pooled baselines went wrong.

    Before per-regime baselines this reported the combustor as worst on ~90 % of
    FD002 engines. With them, the HPC fault mode is recovered correctly.
    """

    from at_data.parse import load_parquet
    from at_data.regimes import load_models

    regimes_path = Path("data/processed/regimes.json")
    if not regimes_path.is_file():
        pytest.skip("regime models not built; run `make data`")

    model = load_models(regimes_path)[subset]
    frame = load_parquet(subset, "train", INTERIM)
    sensor_columns = [f"s{i}" for i in range(1, 22)]

    tally: Counter[str] = Counter()
    for unit in sorted(frame["unit_number"].unique())[:25]:
        rows = frame[frame["unit_number"] == unit]
        sensors_array = rows[sensor_columns].to_numpy()
        regimes = model.predict(rows[["op1", "op2", "op3"]].to_numpy())

        baseline = BaselineAccumulator()
        previous: dict[EngineModule, ComponentState] | None = None
        for index in range(len(sensors_array)):
            sensors = {f"s{i + 1}": float(sensors_array[index, i]) for i in range(21)}
            regime = int(regimes[index])
            if not baseline.is_complete:
                baseline = baseline.observe(compute_proxies(sensors), regime)
            previous = compute_component_health(
                sensors, baseline, previous=previous, cycle=index + 1, regime=regime
            )
        assert previous is not None
        tally[min(previous.items(), key=lambda item: item[1].score)[0].value] += 1

    share = tally["HPC"] / sum(tally.values())
    assert share >= min_share, f"{subset.value}: HPC dominant on only {share:.0%}"


@dataset
def test_health_declines_monotonically_over_life() -> None:
    """Gas-path deterioration is not self-healing; the trend must be downward."""
    from at_data.parse import load_parquet

    frame = load_parquet(Subset.FD001, "train", INTERIM)
    rows = frame[frame["unit_number"] == 1][[f"s{i}" for i in range(1, 22)]].to_numpy()

    baseline = BaselineAccumulator()
    previous: dict[EngineModule, ComponentState] | None = None
    checkpoints: list[float] = []
    for index in range(len(rows)):
        sensors = {f"s{i + 1}": float(rows[index, i]) for i in range(21)}
        if not baseline.is_complete:
            baseline = baseline.observe(compute_proxies(sensors))
        previous = compute_component_health(sensors, baseline, previous=previous, cycle=index + 1)
        if index in (BASELINE_CYCLES, len(rows) // 2, len(rows) - 1):
            checkpoints.append(previous[EngineModule.HPC].score)

    assert checkpoints == sorted(checkpoints, reverse=True), checkpoints
    assert checkpoints[0] - checkpoints[-1] > 30.0, "degradation should be pronounced"
