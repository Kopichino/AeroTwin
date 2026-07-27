"""Tests for the bounded chart history store (Doc 12 section 12.4)."""

from __future__ import annotations

from at_twin.history import (
    CHARTED_SENSORS,
    HISTORY_CAPACITY,
    EngineHistory,
    HistorySample,
    HistoryStore,
)


def sample(cycle: int, health: float = 90.0) -> HistorySample:
    return HistorySample(
        cycle=cycle,
        health_index=health,
        health_band="HEALTHY",
        rul_p50=100.0,
        rul_p10=80.0,
        rul_p90=120.0,
        anomaly_score=1.0,
        model_backed=True,
        sensors={"s3": 1586.0},
        components={"HPC": 95.0},
    )


# ── buffer behaviour ─────────────────────────────────────────────────────────


def test_records_samples_in_order() -> None:
    history = EngineHistory()
    for cycle in range(1, 6):
        history.record(sample(cycle))
    assert [item.cycle for item in history.samples] == [1, 2, 3, 4, 5]


def test_buffer_is_bounded() -> None:
    """A fleet streaming indefinitely must not grow without limit."""
    history = EngineHistory()
    for cycle in range(1, HISTORY_CAPACITY + 250):
        history.record(sample(cycle))
    assert len(history) == HISTORY_CAPACITY


def test_oldest_samples_fall_off_first() -> None:
    history = EngineHistory()
    for cycle in range(1, HISTORY_CAPACITY + 51):
        history.record(sample(cycle))
    assert history.samples[0].cycle == 51
    assert history.samples[-1].cycle == HISTORY_CAPACITY + 50


def test_repeated_cycle_replaces_rather_than_appends() -> None:
    """A tick can revisit a cycle; recording both would put a step in the chart."""
    history = EngineHistory()
    history.record(sample(10, health=90.0))
    history.record(sample(10, health=85.0))

    assert len(history) == 1
    assert history.samples[0].health_index == 85.0


def test_clear_empties_the_buffer() -> None:
    history = EngineHistory()
    history.record(sample(1))
    history.clear()
    assert len(history) == 0


# ── decimation ───────────────────────────────────────────────────────────────


def test_window_returns_everything_when_under_the_limit() -> None:
    history = EngineHistory()
    for cycle in range(1, 21):
        history.record(sample(cycle))
    assert len(history.window(limit=50)) == 20


def test_window_decimates_to_the_limit() -> None:
    history = EngineHistory()
    for cycle in range(1, 501):
        history.record(sample(cycle))
    assert len(history.window(limit=100)) == 100


def test_decimation_always_keeps_the_newest_sample() -> None:
    """The right-hand edge is what the user is watching; dropping it makes the
    chart lag the numbers displayed beside it."""
    history = EngineHistory()
    for cycle in range(1, 501):
        history.record(sample(cycle))
    assert history.window(limit=37)[-1].cycle == 500


def test_decimation_preserves_ordering() -> None:
    history = EngineHistory()
    for cycle in range(1, 401):
        history.record(sample(cycle))
    cycles = [item.cycle for item in history.window(limit=50)]
    assert cycles == sorted(cycles)


def test_from_cycle_filters_older_samples() -> None:
    history = EngineHistory()
    for cycle in range(1, 101):
        history.record(sample(cycle))
    window = history.window(limit=200, from_cycle=90)
    assert all(item.cycle >= 90 for item in window)
    assert len(window) == 11


def test_window_of_empty_history() -> None:
    assert EngineHistory().window() == []


# ── store ────────────────────────────────────────────────────────────────────


def test_store_isolates_engines() -> None:
    store = HistoryStore()
    store.record("a", sample(1, health=90.0))
    store.record("b", sample(1, health=40.0))

    assert store.series("a")[0]["health_index"] == 90.0
    assert store.series("b")[0]["health_index"] == 40.0


def test_unknown_engine_yields_an_empty_series() -> None:
    assert HistoryStore().series("nope") == []
    assert HistoryStore().get("nope") is None


def test_clear_drops_a_recycled_engines_trace() -> None:
    """A recycled twin is a different unit. Splicing its trace onto the retired
    one's would show a phantom recovery from near-failure back to healthy."""
    store = HistoryStore()
    for cycle in range(1, 20):
        store.record("a", sample(cycle, health=100 - cycle * 4))
    store.clear("a")
    assert store.series("a") == []


def test_clearing_an_unknown_engine_is_safe() -> None:
    HistoryStore().clear("never-seen")


def test_store_reports_totals() -> None:
    store = HistoryStore()
    for engine in ("a", "b", "c"):
        for cycle in range(1, 11):
            store.record(engine, sample(cycle))
    assert store.engine_count == 3
    assert store.total_samples == 30


# ── serialisation ────────────────────────────────────────────────────────────


def test_sample_serialises_for_the_wire() -> None:
    payload = sample(42).to_dict()
    assert payload["cycle"] == 42
    assert payload["health_band"] == "HEALTHY"
    assert payload["model_backed"] is True
    assert payload["sensors"]["s3"] == 1586.0


def test_missing_prediction_serialises_as_null() -> None:
    payload = HistorySample(
        cycle=1,
        health_index=90.0,
        health_band="HEALTHY",
        rul_p50=None,
        rul_p10=None,
        rul_p90=None,
        anomaly_score=0.0,
        model_backed=False,
        sensors={},
        components={},
    ).to_dict()
    assert payload["rul_p50"] is None
    assert payload["model_backed"] is False


def test_charted_sensor_list_is_a_subset_of_the_real_channels() -> None:
    from at_core.domain.sensors import SENSOR_BY_KEY

    for key in CHARTED_SENSORS:
        assert key in SENSOR_BY_KEY, f"{key} is not a real C-MAPSS channel"


# ── integration with the registry ────────────────────────────────────────────


def test_registry_records_history_as_twins_advance() -> None:
    from at_core.domain.enums import Subset
    from at_twin.registry import TwinRegistry
    from at_twin.replay import ReplayClock, SyntheticSource

    registry = TwinRegistry(
        SyntheticSource(n_units=3, length=120),
        Subset.FD001,
        clock=ReplayClock(speed=1.0),
        phase_seed=None,
    )
    registry.start_all(0.0)
    for step in range(1, 40):
        registry.tick(step * 1000.0)

    assert registry.history.engine_count == 3
    series = registry.history.series(str(registry.engine_ids[0]))
    assert len(series) > 10
    assert series[0]["cycle"] < series[-1]["cycle"]


def test_history_shows_degradation_over_a_full_life() -> None:
    from at_core.domain.enums import Subset
    from at_twin.registry import TwinRegistry
    from at_twin.replay import ReplayClock, SyntheticSource

    registry = TwinRegistry(
        SyntheticSource(n_units=1, length=150),
        Subset.FD001,
        clock=ReplayClock(speed=1.0),
        phase_seed=None,
        recycle_on_failure=False,
    )
    registry.start_all(0.0)
    for step in range(1, 160):
        registry.tick(step * 1000.0)

    series = registry.history.series(str(registry.engine_ids[0]), limit=600)
    assert series[-1]["health_index"] < series[0]["health_index"] - 20
