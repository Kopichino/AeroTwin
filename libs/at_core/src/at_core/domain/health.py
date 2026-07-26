"""Health Index computation, banding and hysteresis.

Implements Doc 08 section 8.6. Every function here is pure and deterministic so the
entire health model is unit-testable without any I/O (Doc 03 section 3.6 invariant).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from at_core.domain.enums import EngineModule, HealthBand
from at_core.domain.sensors import MODULE_CRITICALITY

# ── Band thresholds (canonical, Doc 03 section 3.1) ──────────────────────────
BAND_HEALTHY_MIN: Final[float] = 80.0
BAND_WATCH_MIN: Final[float] = 60.0
BAND_WARNING_MIN: Final[float] = 35.0

#: Consecutive cycles a candidate band must persist before it latches.
BAND_LATCH_CYCLES: Final[int] = 3

# ── HI fusion weights (must sum to 1.0) ──────────────────────────────────────
W_PHYSICS: Final[float] = 0.30
W_MODEL: Final[float] = 0.40
W_ANOMALY: Final[float] = 0.15
W_WORST_COMPONENT: Final[float] = 0.15

#: EWMA smoothing factor for the health index.
HI_ALPHA: Final[float] = 0.25

#: Maximum permitted per-cycle increase in HI absent a maintenance action.
#: Gas-path deterioration is not self-healing (Doc 08 section 8.6 rationale).
HI_MAX_RECOVERY_PER_CYCLE: Final[float] = 0.5

#: RUL value at which the model term saturates (piecewise label cap, ADR-012).
R_EARLY: Final[float] = 125.0

#: Anomaly score treated as fully saturated (4 sigma).
ANOMALY_SATURATION: Final[float] = 4.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into the inclusive range [low, high]."""
    return max(low, min(high, value))


def band_for(health_index: float) -> HealthBand:
    """Map a health index in [0, 100] to its band, ignoring hysteresis."""
    if health_index >= BAND_HEALTHY_MIN:
        return HealthBand.HEALTHY
    if health_index >= BAND_WATCH_MIN:
        return HealthBand.WATCH
    if health_index >= BAND_WARNING_MIN:
        return HealthBand.WARNING
    return HealthBand.CRITICAL


@dataclass(frozen=True, slots=True)
class ComponentState:
    """Health of a single engine module."""

    module: EngineModule
    score: float
    degradation_rate: float = 0.0
    drivers: tuple[str, ...] = ()
    last_maintained_cycle: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 100.0:
            raise ValueError(f"component score out of range: {self.score}")


@dataclass(frozen=True, slots=True)
class BandTracker:
    """Hysteresis state machine for band transitions.

    A candidate band must be observed for ``BAND_LATCH_CYCLES`` consecutive cycles
    before it becomes the effective band. This prevents dashboard flicker when the
    health index oscillates around a threshold.
    """

    current: HealthBand = HealthBand.HEALTHY
    candidate: HealthBand | None = None
    candidate_streak: int = 0

    def observe(self, health_index: float) -> tuple[BandTracker, bool]:
        """Feed one health-index observation.

        Returns:
            The updated tracker and whether the effective band changed this cycle.
        """
        raw = band_for(health_index)
        if raw is self.current:
            return replace(self, candidate=None, candidate_streak=0), False

        streak = self.candidate_streak + 1 if raw is self.candidate else 1

        if streak >= BAND_LATCH_CYCLES:
            return BandTracker(current=raw, candidate=None, candidate_streak=0), True
        return replace(self, candidate=raw, candidate_streak=streak), False


@dataclass(frozen=True, slots=True)
class HealthInputs:
    """Everything the fusion formula needs for one cycle."""

    component_scores: dict[EngineModule, float] = field(default_factory=dict)
    rul_p50: float | None = None
    anomaly_score: float = 0.0
    previous_hi: float | None = None
    maintenance_applied: bool = False


def physics_term(component_scores: dict[EngineModule, float]) -> float:
    """Criticality-weighted mean of component scores, normalised to [0, 1]."""
    if not component_scores:
        return 1.0
    weighted = 0.0
    total_weight = 0.0
    for module, score in component_scores.items():
        weight = MODULE_CRITICALITY.get(module, 0.0)
        if weight == 0.0:
            continue
        weighted += weight * clamp(score / 100.0)
        total_weight += weight
    if total_weight == 0.0:
        return 1.0
    return weighted / total_weight


def model_term(rul_p50: float | None) -> float:
    """Normalised RUL term. Falls back to 1.0 when no prediction is available."""
    if rul_p50 is None:
        return 1.0
    return clamp(rul_p50 / R_EARLY)


def anomaly_term(anomaly_score: float) -> float:
    """Convert an anomaly score into a health contribution (1.0 = no anomaly)."""
    return 1.0 - clamp(anomaly_score / ANOMALY_SATURATION)


def worst_component_term(component_scores: dict[EngineModule, float]) -> float:
    """Health contribution of the single worst module, normalised to [0, 1]."""
    if not component_scores:
        return 1.0
    return clamp(min(component_scores.values()) / 100.0)


def fuse_health_index(inputs: HealthInputs) -> float:
    """Compute the smoothed, monotonicity-constrained health index.

    The four weighted terms are combined, EWMA-smoothed against the previous value,
    and then constrained so health cannot rise faster than
    ``HI_MAX_RECOVERY_PER_CYCLE`` unless a maintenance action was applied.
    """
    raw = 100.0 * (
        W_PHYSICS * physics_term(inputs.component_scores)
        + W_MODEL * model_term(inputs.rul_p50)
        + W_ANOMALY * anomaly_term(inputs.anomaly_score)
        + W_WORST_COMPONENT * worst_component_term(inputs.component_scores)
    )
    raw = clamp(raw, 0.0, 100.0)

    previous = inputs.previous_hi
    if previous is None:
        return raw

    smoothed = HI_ALPHA * raw + (1.0 - HI_ALPHA) * previous
    if not inputs.maintenance_applied:
        smoothed = min(smoothed, previous + HI_MAX_RECOVERY_PER_CYCLE)
    return clamp(smoothed, 0.0, 100.0)


def failure_probability(rul_p50: float, rul_p90: float | None, horizon: int) -> float:
    """Estimate P(failure within ``horizon`` cycles) from the predictive interval.

    Uses a logistic approximation centred on the median RUL whose steepness is set
    by the interval half-width. When no interval is available a default spread of
    20 percent of the median is assumed. This is replaced by the properly calibrated
    conformal distribution in M5 (Doc 07 section 7.5); the shape is identical so the
    contract does not change.
    """
    import math

    spread = max(1.0, (rul_p90 - rul_p50) if rul_p90 is not None else 0.2 * rul_p50)
    # Logistic CDF evaluated at the horizon.
    return clamp(1.0 / (1.0 + math.exp(-(horizon - rul_p50) / (spread / 2.0))))
