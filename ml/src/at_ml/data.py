"""Dataset construction for RUL models (Doc 07 section 7.2).

Turns the M2 Parquet layer into windowed tensors with per-regime normalisation
and unit-grouped splits. Two rules matter more than anything else here:

1. **Split by unit, never by row.** Consecutive cycles of one engine are almost
   identical, so a random row split leaks the answer and produces beautiful,
   meaningless validation scores.
2. **Normalise within operating regime.** Established in M2: the flight condition
   accounts for roughly 21x more variance in T30 than a whole life of
   degradation (docs/reports/eda.md section 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from at_data.parse import load_parquet
from at_data.regimes import RegimeModel, assign_regimes, fit_regimes

from at_core.domain.enums import Subset
from at_core.domain.sensors import informative_sensors

#: Piecewise RUL cap (ADR-012). Degradation is negligible early in life, so an
#: uncapped label asks the model to distinguish states it cannot observe.
R_EARLY = 125.0

#: Fixed divisor for the age feature. Chosen near the longest observed
#: trajectory (543 cycles in FD004) so the feature lands in roughly [0, 1]
#: without ever referencing a unit's own final cycle.
CYCLE_NORM_REFERENCE = 400.0


@dataclass(frozen=True, slots=True)
class RegimeScaler:
    """Per-regime z-score normaliser fitted on the training split only.

    Fitting on train alone is what keeps the test score honest. The scaler is
    persisted alongside the model so serving applies the identical transform.
    """

    features: tuple[str, ...]
    means: dict[int, np.ndarray]
    stds: dict[int, np.ndarray]
    global_mean: np.ndarray
    global_std: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame, features: tuple[str, ...]) -> RegimeScaler:
        values = frame[list(features)].to_numpy(dtype=np.float32)
        regimes = frame["regime"].to_numpy()

        means: dict[int, np.ndarray] = {}
        stds: dict[int, np.ndarray] = {}
        for regime in np.unique(regimes):
            block = values[regimes == regime]
            means[int(regime)] = block.mean(axis=0)
            # Guard against channels that are constant within a regime.
            stds[int(regime)] = np.maximum(block.std(axis=0), 1e-6)

        return cls(
            features=features,
            means=means,
            stds=stds,
            global_mean=values.mean(axis=0),
            global_std=np.maximum(values.std(axis=0), 1e-6),
        )

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        """Apply per-regime standardisation, falling back to global statistics."""
        values = frame[list(self.features)].to_numpy(dtype=np.float32)
        regimes = frame["regime"].to_numpy()
        out = np.empty_like(values)

        for regime in np.unique(regimes):
            mask = regimes == regime
            mean = self.means.get(int(regime), self.global_mean)
            std = self.stds.get(int(regime), self.global_std)
            out[mask] = (values[mask] - mean) / std

        return np.asarray(out, dtype=np.float32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": list(self.features),
            "means": {str(k): v.tolist() for k, v in self.means.items()},
            "stds": {str(k): v.tolist() for k, v in self.stds.items()},
            "global_mean": self.global_mean.tolist(),
            "global_std": self.global_std.tolist(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RegimeScaler:
        return cls(
            features=tuple(payload["features"]),
            means={int(k): np.asarray(v, dtype=np.float32) for k, v in payload["means"].items()},
            stds={int(k): np.asarray(v, dtype=np.float32) for k, v in payload["stds"].items()},
            global_mean=np.asarray(payload["global_mean"], dtype=np.float32),
            global_std=np.asarray(payload["global_std"], dtype=np.float32),
        )


@dataclass(frozen=True, slots=True)
class WindowSet:
    """Windowed tensors ready for a model."""

    x: np.ndarray  # (N, W, F) float32
    y: np.ndarray  # (N,) float32, capped RUL
    units: np.ndarray  # (N,) int, provenance for grouped splits
    features: tuple[str, ...]
    window: int

    def __len__(self) -> int:
        return int(self.x.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.x.shape[2])

    def subset_by_units(self, units: set[int]) -> WindowSet:
        mask = np.isin(self.units, list(units))
        return WindowSet(
            x=self.x[mask],
            y=self.y[mask],
            units=self.units[mask],
            features=self.features,
            window=self.window,
        )


def add_engineered_features(frame: pd.DataFrame, sensors: tuple[str, ...]) -> pd.DataFrame:
    """Add first differences, rolling means and normalised age.

    Ablated in the comparison report rather than assumed useful: the report
    records the delta these features actually buy.
    """
    result = frame.copy()
    grouped = result.groupby("unit_number")

    for sensor in sensors:
        result[f"{sensor}_d"] = grouped[sensor].diff().fillna(0.0).astype(np.float32)
        result[f"{sensor}_m5"] = (
            grouped[sensor]
            .transform(lambda s: s.rolling(5, min_periods=1).mean())
            .astype(np.float32)
        )

    # Absolute age, scaled by a *constant*, never by the unit's own maximum.
    #
    # An earlier version used `time_in_cycles / max(time_in_cycles)` per unit.
    # That leaks the target: in training, max() is the failure cycle, so
    # cycle_norm = 1.0 means "dead". In test, max() is merely where recording
    # stopped, so cycle_norm = 1.0 means "alive with ~112 cycles left". The model
    # learned cycle_norm ~ 1 => RUL ~ 0 and predicted every test engine as nearly
    # failed: validation RMSE 11, test RMSE 54, R2 -0.80.
    #
    # Dividing by a fixed reference keeps the feature meaningful and identical
    # across splits, and is computable online from a single cycle counter.
    result["cycle_norm"] = (
        result["time_in_cycles"].to_numpy(dtype=np.float32) / CYCLE_NORM_REFERENCE
    )
    return result


def build_windows(
    frame: pd.DataFrame,
    scaler: RegimeScaler,
    window: int,
    *,
    last_only: bool = False,
) -> WindowSet:
    """Slice each unit's trajectory into overlapping windows.

    Args:
        last_only: Take only the final window per unit, the standard C-MAPSS test
            protocol. The label file gives remaining life at the last observed
            cycle, so that is the only point with ground truth.

    Short trajectories are left-padded by repeating the first row so every unit
    stays scoreable. FD004 has two 19-cycle test units, which is exactly why
    ADR-013 was amended in M2.
    """
    scaled = scaler.transform(frame)
    targets = np.minimum(frame["rul"].to_numpy(dtype=np.float32), R_EARLY)
    unit_numbers = frame["unit_number"].to_numpy()

    xs: list[np.ndarray] = []
    ys: list[float] = []
    us: list[int] = []

    for unit in np.unique(unit_numbers):
        mask = unit_numbers == unit
        block = scaled[mask]
        target_block = targets[mask]
        length = block.shape[0]

        if length < window:
            pad = np.repeat(block[:1], window - length, axis=0)
            block = np.concatenate([pad, block], axis=0)
            target_block = np.concatenate(
                [np.repeat(target_block[:1], window - length), target_block]
            )
            length = window

        starts = [length - window] if last_only else range(length - window + 1)
        for start in starts:
            xs.append(block[start : start + window])
            ys.append(float(target_block[start + window - 1]))
            us.append(int(unit))

    return WindowSet(
        x=np.stack(xs).astype(np.float32),
        y=np.asarray(ys, dtype=np.float32),
        units=np.asarray(us),
        features=scaler.features,
        window=window,
    )


@dataclass(frozen=True, slots=True)
class PreparedData:
    """Everything a training run needs for one subset."""

    subset: Subset
    train: WindowSet
    val: WindowSet
    test: WindowSet
    scaler: RegimeScaler
    regime_model: RegimeModel
    train_units: tuple[int, ...]
    val_units: tuple[int, ...]


def prepare(
    subset: Subset,
    interim: Path,
    *,
    val_fraction: float = 0.2,
    seed: int = 42,
    engineered: bool = True,
) -> PreparedData:
    """Load, normalise, window and split one subset.

    The validation split holds out whole **units**, so validation measures
    generalisation to unseen engines rather than interpolation between adjacent
    cycles of engines the model already memorised.
    """
    train_raw = load_parquet(subset, "train", interim)
    test_raw = load_parquet(subset, "test", interim)

    regime_model = fit_regimes(train_raw, subset)
    train_raw = assign_regimes(train_raw, regime_model)
    test_raw = assign_regimes(test_raw, regime_model)

    sensors = informative_sensors(subset)
    if engineered:
        train_raw = add_engineered_features(train_raw, sensors)
        test_raw = add_engineered_features(test_raw, sensors)
        features: tuple[str, ...] = (
            *sensors,
            *(f"{s}_d" for s in sensors),
            *(f"{s}_m5" for s in sensors),
            "cycle_norm",
        )
    else:
        features = sensors

    scaler = RegimeScaler.fit(train_raw, features)
    window = subset.window_size

    all_units = np.unique(train_raw["unit_number"].to_numpy())
    shuffled = np.random.default_rng(seed).permutation(all_units)
    n_val = max(1, int(len(shuffled) * val_fraction))
    val_units = {int(u) for u in shuffled[:n_val]}
    train_units = {int(u) for u in shuffled[n_val:]}

    full = build_windows(train_raw, scaler, window)
    test = build_windows(test_raw, scaler, window, last_only=True)

    return PreparedData(
        subset=subset,
        train=full.subset_by_units(train_units),
        val=full.subset_by_units(val_units),
        test=test,
        scaler=scaler,
        regime_model=regime_model,
        train_units=tuple(sorted(train_units)),
        val_units=tuple(sorted(val_units)),
    )
