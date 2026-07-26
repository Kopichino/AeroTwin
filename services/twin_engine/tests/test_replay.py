"""Tests for the replay clock, telemetry sources and cursors (Doc 08 section 8.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from at_core.domain.enums import Subset
from at_twin.replay import (
    ALLOWED_SPEEDS,
    Cursor,
    ReplayClock,
    SyntheticSource,
    assign_phase_offsets,
)

INTERIM = Path("data/interim")
dataset = pytest.mark.skipif(
    not (INTERIM / "FD001_train.parquet").is_file(),
    reason="interim parquet not present; run `make data`",
)


# ── clock ────────────────────────────────────────────────────────────────────


def test_clock_advances_one_cycle_per_second_at_1x() -> None:
    clock = ReplayClock(speed=1.0)
    assert clock.cycles_at(1000.0) == pytest.approx(1.0)
    assert clock.cycles_at(10_000.0) == pytest.approx(10.0)


def test_speed_multiplies_cycle_rate() -> None:
    assert ReplayClock(speed=8.0).cycles_at(1000.0) == pytest.approx(8.0)
    assert ReplayClock(speed=0.5).cycles_at(1000.0) == pytest.approx(0.5)


def test_invalid_speed_is_rejected() -> None:
    with pytest.raises(ValueError, match="speed must be one of"):
        ReplayClock(speed=3.0)


def test_invalid_cycle_duration_is_rejected() -> None:
    with pytest.raises(ValueError, match="cycle_duration_ms"):
        ReplayClock(cycle_duration_ms=0)


@pytest.mark.parametrize("speed", sorted(ALLOWED_SPEEDS))
def test_every_allowed_speed_constructs(speed: float) -> None:
    assert ReplayClock(speed=speed).speed == speed


def test_speed_change_banks_progress_and_never_rewinds() -> None:
    """Changing speed mid-run must not move the engine backwards."""
    clock = ReplayClock(speed=1.0)
    at_change = clock.cycles_at(10_000.0)
    faster = clock.with_speed(8.0, 10_000.0)
    assert faster.cycles_at(10_000.0) == pytest.approx(at_change)
    assert faster.cycles_at(11_000.0) == pytest.approx(at_change + 8.0)


def test_pause_freezes_the_cycle_position() -> None:
    clock = ReplayClock(speed=1.0).pause(5000.0)
    assert clock.cycles_at(5000.0) == pytest.approx(5.0)
    assert clock.cycles_at(60_000.0) == pytest.approx(5.0)


def test_resume_continues_without_jumping() -> None:
    paused = ReplayClock(speed=1.0).pause(5000.0)
    resumed = paused.resume(60_000.0)
    assert resumed.cycles_at(60_000.0) == pytest.approx(5.0)
    assert resumed.cycles_at(61_000.0) == pytest.approx(6.0)


def test_pause_is_idempotent() -> None:
    once = ReplayClock(speed=1.0).pause(5000.0)
    assert once.pause(9000.0).cycles_at(9000.0) == pytest.approx(5.0)


def test_resume_when_not_paused_is_a_no_op() -> None:
    clock = ReplayClock(speed=1.0)
    assert clock.resume(1000.0) is clock


def test_seek_sets_an_absolute_position() -> None:
    clock = ReplayClock(speed=1.0).seek(100.0, 0.0)
    assert clock.cycles_at(0.0) == pytest.approx(100.0)
    assert clock.cycles_at(1000.0) == pytest.approx(101.0)


def test_seek_cannot_go_negative() -> None:
    assert ReplayClock().seek(-50.0, 0.0).cycles_at(0.0) == 0.0


def test_tick_interval_shrinks_with_speed() -> None:
    assert ReplayClock(speed=1.0).tick_interval_ms() == 1000.0
    assert ReplayClock(speed=8.0).tick_interval_ms() == 125.0


def test_clock_is_immutable() -> None:
    clock = ReplayClock(speed=1.0)
    clock.with_speed(8.0, 0.0)
    assert clock.speed == 1.0


# ── cursor ───────────────────────────────────────────────────────────────────


def test_cursor_advances_forward_only() -> None:
    cursor = Cursor(unit=1, cycle=50, total_cycles=100)
    assert cursor.advance_to(60).cycle == 60
    assert cursor.advance_to(40).cycle == 50, "must never move backwards"


def test_cursor_stops_at_end_of_trajectory() -> None:
    cursor = Cursor(unit=1, cycle=90, total_cycles=100)
    assert cursor.advance_to(500).cycle == 100
    assert cursor.advance_to(500).at_end


def test_cursor_seek_is_bounded() -> None:
    cursor = Cursor(unit=1, cycle=0, total_cycles=100)
    assert cursor.seek(150).cycle == 100
    assert cursor.seek(-5).cycle == 0


def test_cursor_without_known_length_is_never_at_end() -> None:
    assert not Cursor(unit=1, cycle=999, total_cycles=0).at_end


# ── phase offsets ────────────────────────────────────────────────────────────


def test_phase_offsets_are_deterministic() -> None:
    units = tuple(range(1, 51))
    lengths = dict.fromkeys(units, 200)
    assert assign_phase_offsets(units, lengths, seed=7) == assign_phase_offsets(
        units, lengths, seed=7
    )


def test_phase_offsets_span_a_realistic_age_mix() -> None:
    """A demo fleet must not open as 260 identical brand-new engines."""
    units = tuple(range(1, 201))
    lengths = dict.fromkeys(units, 200)
    offsets = assign_phase_offsets(units, lengths, seed=42)
    fractions = [offset / 200 for offset in offsets.values()]

    assert min(fractions) < 0.10, "some engines should be nearly new"
    assert max(fractions) > 0.60, "some engines should be near overhaul"
    assert 0.15 < sum(fractions) / len(fractions) < 0.45, "fleet should skew mid-life"


def test_phase_offsets_never_exceed_trajectory() -> None:
    units = tuple(range(1, 30))
    lengths = {unit: 50 + unit for unit in units}
    for unit, offset in assign_phase_offsets(units, lengths, seed=1).items():
        assert 0 <= offset < lengths[unit]


def test_zero_length_unit_gets_zero_offset() -> None:
    assert assign_phase_offsets((1,), {1: 0})[1] == 0


# ── synthetic source ─────────────────────────────────────────────────────────


def test_synthetic_source_shape() -> None:
    source = SyntheticSource(n_units=3, length=50)
    assert source.units() == (1, 2, 3)
    assert source.length(1) == 50


def test_synthetic_source_is_deterministic() -> None:
    first = SyntheticSource(seed=1).read(1, 10)
    second = SyntheticSource(seed=1).read(1, 10)
    assert first is not None and second is not None
    assert first.sensors == second.sensors


def test_synthetic_source_degrades_over_life() -> None:
    source = SyntheticSource(n_units=1, length=100)
    early, late = source.read(1, 1), source.read(1, 100)
    assert early is not None and late is not None
    assert late.sensors["s3"] > early.sensors["s3"], "T30 rises as HPC degrades"
    assert late.sensors["s20"] < early.sensors["s20"], "coolant bleed falls"


def test_synthetic_source_bounds() -> None:
    source = SyntheticSource(n_units=2, length=10)
    assert source.read(1, 0) is None
    assert source.read(1, 11) is None
    assert source.read(99, 1) is None


# ── C-MAPSS source ───────────────────────────────────────────────────────────


@dataset
def test_cmapss_source_loads_all_units() -> None:
    from at_twin.replay import CmapssFileSource

    source = CmapssFileSource(Subset.FD001, INTERIM, "train")
    assert len(source.units()) == 100
    assert source.units()[0] == 1


@dataset
def test_cmapss_source_returns_real_values() -> None:
    from at_twin.replay import CmapssFileSource

    row = CmapssFileSource(Subset.FD001, INTERIM, "train").read(1, 1)
    assert row is not None
    assert row.cycle == 1
    # Verified against the raw file: unit 1 cycle 1 has T30 = 1589.70.
    assert row.sensors["s3"] == pytest.approx(1589.70, abs=0.01)
    assert len(row.sensors) == 21


@dataset
def test_cmapss_source_respects_trajectory_length() -> None:
    from at_twin.replay import CmapssFileSource

    source = CmapssFileSource(Subset.FD001, INTERIM, "train")
    length = source.length(1)
    assert source.read(1, length) is not None
    assert source.read(1, length + 1) is None
