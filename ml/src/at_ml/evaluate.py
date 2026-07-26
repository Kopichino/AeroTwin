"""Evaluation metrics for RUL prediction (Doc 07 section 7.4).

RMSE alone is a poor summary for prognostics. Two additions matter:

* **NASA asymmetric score** punishes late predictions far harder than early ones,
  because predicting more life than an engine has is the failure mode that
  strands an aircraft.
* **Per-horizon RMSE** reveals whether the model is accurate *near failure*,
  which is the only region that drives a maintenance decision. A model can post
  a good overall RMSE purely by being accurate on healthy engines.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Metrics:
    """Evaluation summary for one model on one split."""

    rmse: float
    mae: float
    nasa_score: float
    r2: float
    rmse_critical: float
    """RMSE restricted to true RUL <= 25 cycles, the decision-critical band."""
    rmse_mid: float
    rmse_healthy: float
    late_fraction: float
    """Share of predictions that overestimate remaining life."""
    n: int

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def nasa_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Official C-MAPSS asymmetric scoring function.

        d = y_pred - y_true
        s = exp(-d / 13) - 1   if d < 0   (early, conservative)
            exp( d / 10) - 1   if d >= 0  (late, dangerous)

    Lower is better. The asymmetry is the point: an engine predicted to have
    more life than it does gets grounded unexpectedly.
    """
    d = np.asarray(y_pred, dtype=np.float64) - np.asarray(y_true, dtype=np.float64)
    early = np.expm1(-d / 13.0)
    late = np.expm1(d / 10.0)
    return float(np.where(d < 0, early, late).sum())


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if y_true.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((y_pred - y_true) ** 2)))


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> Metrics:
    """Compute the full metric suite."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    residual = y_pred - y_true
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))

    critical = y_true <= 25
    mid = (y_true > 25) & (y_true <= 75)
    healthy = y_true > 75

    return Metrics(
        rmse=_rmse(y_true, y_pred),
        mae=float(np.mean(np.abs(residual))),
        nasa_score=nasa_score(y_true, y_pred),
        r2=float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0,
        rmse_critical=_rmse(y_true[critical], y_pred[critical]),
        rmse_mid=_rmse(y_true[mid], y_pred[mid]),
        rmse_healthy=_rmse(y_true[healthy], y_pred[healthy]),
        late_fraction=float(np.mean(residual > 0)),
        n=int(y_true.size),
    )


def prediction_interval_coverage(
    y_true: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> tuple[float, float]:
    """PICP and mean interval width for calibration assessment.

    A useful interval must both cover the truth at its nominal rate and stay
    tight enough to inform a decision; reporting coverage alone would let a
    trivially wide interval look well calibrated.
    """
    inside = (y_true >= lower) & (y_true <= upper)
    return float(np.mean(inside)), float(np.mean(upper - lower))
