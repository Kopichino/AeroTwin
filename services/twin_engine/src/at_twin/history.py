"""Bounded in-memory time series per twin (Doc 12 section 12.4).

The engine detail view needs history: sensor traces, a health curve, and an RUL
timeline with its confidence band. Postgres is the eventual home for this
(Doc 04), but until the persistence layer lands the twin engine keeps a bounded
ring buffer per engine so the charts have something real to draw.

Two properties matter:

* **Bounded.** A fleet of 260 engines streaming indefinitely must not grow
  without limit. Each engine keeps a fixed number of samples and old ones fall
  off the back.
* **Downsampled on read, not on write.** Writes happen every cycle and must stay
  cheap; a chart asking for 200 points from a 600-sample buffer decimates at
  query time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: Samples retained per engine. At 8x replay this is roughly ten minutes of
#: wall-clock history, comfortably more than any chart displays at once.
HISTORY_CAPACITY = 600

#: Charted sensors. The full 21 would quadruple memory for channels that are
#: either constant or not plotted; the rest remain available in the live delta.
CHARTED_SENSORS = ("s2", "s3", "s4", "s7", "s8", "s9", "s11", "s12", "s15", "s17", "s20", "s21")


@dataclass(frozen=True, slots=True)
class HistorySample:
    """One cycle of charted state for one engine."""

    cycle: int
    health_index: float
    health_band: str
    rul_p50: float | None
    rul_p10: float | None
    rul_p90: float | None
    anomaly_score: float
    model_backed: bool
    sensors: dict[str, float]
    components: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "health_index": round(self.health_index, 2),
            "health_band": self.health_band,
            "rul_p50": round(self.rul_p50, 1) if self.rul_p50 is not None else None,
            "rul_p10": round(self.rul_p10, 1) if self.rul_p10 is not None else None,
            "rul_p90": round(self.rul_p90, 1) if self.rul_p90 is not None else None,
            "anomaly_score": round(self.anomaly_score, 2),
            "model_backed": self.model_backed,
            "sensors": {k: round(v, 3) for k, v in self.sensors.items()},
            "components": {k: round(v, 1) for k, v in self.components.items()},
        }


@dataclass(slots=True)
class EngineHistory:
    """Ring buffer of samples for one engine."""

    samples: deque[HistorySample] = field(default_factory=lambda: deque(maxlen=HISTORY_CAPACITY))

    def record(self, sample: HistorySample) -> None:
        """Append a sample, replacing the last one if the cycle repeats.

        A tick can revisit the same cycle when the clock has not advanced far
        enough; recording both would put a vertical step in every chart.
        """
        if self.samples and self.samples[-1].cycle == sample.cycle:
            self.samples[-1] = sample
        else:
            self.samples.append(sample)

    def clear(self) -> None:
        self.samples.clear()

    def __len__(self) -> int:
        return len(self.samples)

    def window(self, limit: int = 200, from_cycle: int | None = None) -> list[HistorySample]:
        """Return at most ``limit`` samples, evenly decimated.

        Decimation keeps the newest sample regardless of stride, because the
        right-hand edge of a live chart is the part the user is actually
        watching. Dropping it to keep an even stride would make the chart lag
        the numbers displayed beside it.
        """
        rows = list(self.samples)
        if from_cycle is not None:
            rows = [row for row in rows if row.cycle >= from_cycle]

        if len(rows) <= limit:
            return rows

        stride = len(rows) / limit
        picked = [rows[int(index * stride)] for index in range(limit)]
        if picked[-1].cycle != rows[-1].cycle:
            picked[-1] = rows[-1]
        return picked


class HistoryStore:
    """Per-engine histories, keyed by engine id."""

    def __init__(self) -> None:
        self._by_engine: dict[str, EngineHistory] = {}

    def record(self, engine_id: str, sample: HistorySample) -> None:
        history = self._by_engine.get(engine_id)
        if history is None:
            history = EngineHistory()
            self._by_engine[engine_id] = history
        history.record(sample)

    def clear(self, engine_id: str) -> None:
        """Drop an engine's history.

        Called when a twin is recycled: the replacement engine is a different
        physical unit, and splicing its trace onto the retired one's would show
        a phantom recovery from near-failure back to healthy.
        """
        self._by_engine.get(engine_id, EngineHistory()).clear()

    def get(self, engine_id: str) -> EngineHistory | None:
        return self._by_engine.get(engine_id)

    def series(
        self, engine_id: str, *, limit: int = 200, from_cycle: int | None = None
    ) -> list[dict[str, Any]]:
        history = self._by_engine.get(engine_id)
        if history is None:
            return []
        return [sample.to_dict() for sample in history.window(limit, from_cycle)]

    @property
    def engine_count(self) -> int:
        return len(self._by_engine)

    @property
    def total_samples(self) -> int:
        return sum(len(history) for history in self._by_engine.values())
