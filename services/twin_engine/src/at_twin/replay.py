"""Replay clock and telemetry sources (Doc 08 section 8.8).

The replay clock maps wall-clock time onto engine cycles at a speed multiplier,
so a 200-cycle trajectory that represents months of real flying can be watched in
minutes. It is deliberately a *logical* clock: everything downstream records both
``wall_ts`` and ``cycle``, and seek/pause/resume manipulate the logical clock
without any dependency on real elapsed time.

Pure and deterministic: identical inputs produce identical cycle sequences, which
is what makes event-sourced replay exact.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import numpy as np
from at_data.parse import Split

from at_core.domain.enums import Subset

SENSOR_KEYS: Final[tuple[str, ...]] = tuple(f"s{i}" for i in range(1, 22))
OP_KEYS: Final[tuple[str, ...]] = ("op1", "op2", "op3")

#: Wall-clock milliseconds that one engine cycle represents at 1x speed.
DEFAULT_CYCLE_DURATION_MS: Final[int] = 1000

ALLOWED_SPEEDS: Final[frozenset[float]] = frozenset({0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0})


@dataclass(frozen=True, slots=True)
class ReplayClock:
    """Virtual clock converting elapsed wall time into engine cycles.

    ``accumulated_cycles`` holds progress banked before the most recent
    speed change or pause, so changing speed mid-run never rewinds the engine.
    """

    speed: float = 1.0
    cycle_duration_ms: int = DEFAULT_CYCLE_DURATION_MS
    epoch_ms: float = 0.0
    accumulated_cycles: float = 0.0
    paused: bool = False

    def __post_init__(self) -> None:
        if self.speed not in ALLOWED_SPEEDS:
            raise ValueError(f"speed must be one of {sorted(ALLOWED_SPEEDS)}, got {self.speed}")
        if self.cycle_duration_ms <= 0:
            raise ValueError("cycle_duration_ms must be positive")

    def cycles_at(self, now_ms: float) -> float:
        """Fractional cycle position at wall-clock time ``now_ms``."""
        if self.paused:
            return self.accumulated_cycles
        elapsed = max(0.0, now_ms - self.epoch_ms)
        return self.accumulated_cycles + (elapsed * self.speed) / self.cycle_duration_ms

    def with_speed(self, speed: float, now_ms: float) -> ReplayClock:
        """Change speed, banking progress so far so the position is preserved."""
        banked = self.cycles_at(now_ms)
        return replace(self, speed=speed, epoch_ms=now_ms, accumulated_cycles=banked)

    def pause(self, now_ms: float) -> ReplayClock:
        if self.paused:
            return self
        return replace(
            self, paused=True, accumulated_cycles=self.cycles_at(now_ms), epoch_ms=now_ms
        )

    def resume(self, now_ms: float) -> ReplayClock:
        if not self.paused:
            return self
        return replace(self, paused=False, epoch_ms=now_ms)

    def seek(self, cycle: float, now_ms: float) -> ReplayClock:
        """Jump to an absolute cycle position."""
        return replace(self, accumulated_cycles=max(0.0, cycle), epoch_ms=now_ms)

    def tick_interval_ms(self) -> float:
        """Wall-clock milliseconds between consecutive cycles at this speed."""
        return self.cycle_duration_ms / self.speed


@dataclass(frozen=True, slots=True)
class TelemetryRow:
    """One cycle of telemetry for one engine."""

    unit_number: int
    cycle: int
    op_settings: tuple[float, float, float]
    sensors: dict[str, float]


class TelemetrySource(ABC):
    """Interface for anything that can supply engine telemetry by cycle.

    Implemented by the C-MAPSS file source today; a Kafka or OPC-UA source could
    be substituted without the twin engine noticing.
    """

    @abstractmethod
    def units(self) -> tuple[int, ...]:
        """All unit numbers available from this source."""

    @abstractmethod
    def length(self, unit: int) -> int:
        """Total number of cycles in this unit's trajectory."""

    @abstractmethod
    def read(self, unit: int, cycle: int) -> TelemetryRow | None:
        """Return the row at ``cycle`` (1-based), or None past end of trajectory."""


class CmapssFileSource(TelemetrySource):
    """In-memory C-MAPSS telemetry, indexed for O(1) cycle lookup.

    The whole subset is loaded once into contiguous numpy arrays. FD004 train is
    the largest at 61k rows by 24 float32 columns, roughly 6 MB, so holding every
    subset in memory is cheap and removes I/O from the tick loop entirely.
    """

    def __init__(self, subset: Subset, interim_dir: Path, split: Split = "train") -> None:
        from at_data.parse import load_parquet

        self.subset = subset
        self.split = split

        frame = load_parquet(subset, split, interim_dir).sort_values(
            ["unit_number", "time_in_cycles"]
        )
        self._columns = (*OP_KEYS, *SENSOR_KEYS)
        self._values: np.ndarray = frame[list(self._columns)].to_numpy(dtype=np.float32)

        unit_numbers = frame["unit_number"].to_numpy()
        self._offsets: dict[int, tuple[int, int]] = {}
        boundaries = np.flatnonzero(np.diff(unit_numbers)) + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [len(unit_numbers)]))
        for start, end in zip(starts, ends, strict=True):
            self._offsets[int(unit_numbers[start])] = (int(start), int(end))

        self._units = tuple(sorted(self._offsets))

    def units(self) -> tuple[int, ...]:
        return self._units

    def length(self, unit: int) -> int:
        start, end = self._offsets.get(unit, (0, 0))
        return end - start

    def read(self, unit: int, cycle: int) -> TelemetryRow | None:
        bounds = self._offsets.get(unit)
        if bounds is None or cycle < 1:
            return None
        start, end = bounds
        index = start + cycle - 1
        if index >= end:
            return None

        row = self._values[index]
        return TelemetryRow(
            unit_number=unit,
            cycle=cycle,
            op_settings=(float(row[0]), float(row[1]), float(row[2])),
            sensors={key: float(row[i + 3]) for i, key in enumerate(SENSOR_KEYS)},
        )


class SyntheticSource(TelemetrySource):
    """Deterministic synthetic telemetry for tests and offline demos.

    Produces a linear degradation ramp with seeded noise, so tests never depend on
    the dataset being downloaded.
    """

    def __init__(self, n_units: int = 3, length: int = 100, seed: int = 42) -> None:
        self._units = tuple(range(1, n_units + 1))
        self._length = length
        self._rng_seed = seed

    def units(self) -> tuple[int, ...]:
        return self._units

    def length(self, unit: int) -> int:
        return self._length

    def read(self, unit: int, cycle: int) -> TelemetryRow | None:
        if unit not in self._units or not 1 <= cycle <= self._length:
            return None

        progress = (cycle - 1) / max(1, self._length - 1)
        rng = np.random.default_rng(self._rng_seed + unit * 100_000 + cycle)
        noise = rng.normal(0.0, 0.05)

        sensors = {
            "s2": 642.0 + 1.4 * progress + noise * 0.1,
            "s3": 1586.0 + 14.0 * progress + noise,
            "s4": 1400.0 + 26.0 * progress + noise,
            "s7": 554.0 - 3.0 * progress + noise * 0.1,
            "s8": 2388.0 + noise * 0.01,
            "s9": 9050.0 - 10.0 * progress + noise,
            "s11": 47.2 + 0.9 * progress + noise * 0.01,
            "s12": 522.0 - 2.2 * progress + noise * 0.1,
            "s13": 2388.0 + noise * 0.01,
            "s14": 8132.0 - 18.0 * progress + noise,
            "s15": 8.41 + 0.11 * progress + noise * 0.001,
            "s17": 392.0 + 4.0 * progress,
            "s20": 39.0 - 0.5 * progress + noise * 0.01,
            "s21": 23.4 - 0.35 * progress + noise * 0.01,
        }
        return TelemetryRow(
            unit_number=unit,
            cycle=cycle,
            op_settings=(0.0, 0.0, 100.0),
            sensors=sensors,
        )


@dataclass(frozen=True, slots=True)
class Cursor:
    """Per-unit playback position and end-of-life policy."""

    unit: int
    cycle: int = 0
    total_cycles: int = 0
    phase_offset: int = 0
    """Starting cycle, so a freshly seeded fleet shows a realistic age mix."""

    @property
    def at_end(self) -> bool:
        return self.total_cycles > 0 and self.cycle >= self.total_cycles

    def advance_to(self, target_cycle: int) -> Cursor:
        """Move forward to ``target_cycle``, never past end of trajectory."""
        capped = min(target_cycle, self.total_cycles) if self.total_cycles else target_cycle
        return replace(self, cycle=max(self.cycle, capped))

    def seek(self, cycle: int) -> Cursor:
        bounded = max(0, min(cycle, self.total_cycles or cycle))
        return replace(self, cycle=bounded)


#: Fleet age mix at seeding. A real fleet is mostly mid-life with a minority of
#: new and near-overhaul engines, so the demo opens on a plausible distribution
#: rather than a wall of identical healthy cards.
FLEET_AGE_MIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.30, 0.00, 0.15),  # 30 % recently installed
    (0.45, 0.15, 0.45),  # 45 % mid-life
    (0.20, 0.45, 0.70),  # 20 % ageing
    (0.05, 0.70, 0.85),  # 5 %  near overhaul
)


def assign_phase_offsets(
    units: tuple[int, ...],
    lengths: dict[int, int],
    *,
    seed: int = 42,
    age_mix: tuple[tuple[float, float, float], ...] = FLEET_AGE_MIX,
) -> dict[int, int]:
    """Stagger fleet start positions so engines span new to near-failure.

    Sampling uniformly across 0-85 % of life was tried first and rejected: at 8x
    speed most of the fleet reached end of trajectory within a few minutes, so a
    demo would open on a healthy fleet and end on a graveyard. The banded mix
    above keeps a realistic standing distribution for far longer.

    Seeded, so a demo is reproducible run to run.
    """
    rng = np.random.default_rng(seed)
    weights = [band[0] for band in age_mix]
    offsets: dict[int, int] = {}

    for unit in units:
        total = lengths.get(unit, 0)
        if total <= 0:
            offsets[unit] = 0
            continue
        _, low, high = age_mix[rng.choice(len(age_mix), p=weights)]
        offsets[unit] = int(rng.uniform(low, high) * total)

    return offsets
