"""C-MAPSS raw text -> typed DataFrame -> Parquet.

The raw files are whitespace-delimited with no header and 26 columns. This module
gives them names, types, per-unit RUL labels and the piecewise-linear target used
by the RUL models (ADR-012), then persists them as Parquet for fast reload.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from at_core.domain.enums import Subset
from at_core.domain.sensors import N_OP_SETTINGS, N_SENSORS

Split = Literal["train", "test"]

#: Cap applied to the RUL target. Degradation is negligible early in life, so an
#: uncapped linear label injects noise; capping is the C-MAPSS literature standard
#: and keeps our metrics comparable to published results (ADR-012).
R_EARLY = 125

ID_COLUMNS = ("unit_number", "time_in_cycles")
OP_COLUMNS = tuple(f"op{i}" for i in range(1, N_OP_SETTINGS + 1))
SENSOR_COLUMNS = tuple(f"s{i}" for i in range(1, N_SENSORS + 1))
ALL_COLUMNS = (*ID_COLUMNS, *OP_COLUMNS, *SENSOR_COLUMNS)

DTYPES: dict[str, str] = {
    "unit_number": "int32",
    "time_in_cycles": "int32",
    **dict.fromkeys((*OP_COLUMNS, *SENSOR_COLUMNS), "float32"),
}


@dataclass(frozen=True, slots=True)
class SubsetFrames:
    """Parsed train and test frames for one subset, with labels attached."""

    subset: Subset
    train: pd.DataFrame
    test: pd.DataFrame

    @property
    def train_units(self) -> int:
        return int(self.train["unit_number"].nunique())

    @property
    def test_units(self) -> int:
        return int(self.test["unit_number"].nunique())


def read_raw(path: Path) -> pd.DataFrame:
    """Read one raw C-MAPSS telemetry file into a typed DataFrame.

    The files have trailing whitespace producing two phantom NaN columns in some
    releases, so columns are assigned positionally after dropping all-NaN columns.
    """
    frame = pd.read_csv(path, sep=r"\s+", header=None, engine="c")
    frame = frame.dropna(axis=1, how="all")

    if frame.shape[1] != len(ALL_COLUMNS):
        raise ValueError(f"{path.name}: expected {len(ALL_COLUMNS)} columns, got {frame.shape[1]}")

    frame.columns = list(ALL_COLUMNS)
    return frame.astype(DTYPES)


def read_rul_labels(path: Path) -> np.ndarray:
    """Read the ground-truth RUL file for a test subset (one value per unit)."""
    values = pd.read_csv(path, sep=r"\s+", header=None).iloc[:, 0].to_numpy()
    result: np.ndarray = values.astype(np.int32)
    return result


def add_train_rul(frame: pd.DataFrame, *, cap: int = R_EARLY) -> pd.DataFrame:
    """Attach RUL targets to a training frame.

    For training units the trajectory runs to failure, so RUL at cycle t is simply
    ``max_cycle - t``. ``rul`` is the raw value and ``rul_capped`` the piecewise
    target the models actually regress.
    """
    result = frame.copy()
    max_cycle = result.groupby("unit_number")["time_in_cycles"].transform("max")
    result["rul"] = (max_cycle - result["time_in_cycles"]).astype("int32")
    result["rul_capped"] = result["rul"].clip(upper=cap).astype("int32")
    return result


def add_test_rul(
    frame: pd.DataFrame, rul_at_end: np.ndarray, *, cap: int = R_EARLY
) -> pd.DataFrame:
    """Attach RUL targets to a test frame.

    Test trajectories stop *before* failure. The label file gives the remaining
    life at the final observed cycle, so RUL at cycle t is
    ``(max_cycle - t) + rul_at_end``.
    """
    result = frame.copy()
    units = np.sort(result["unit_number"].unique())

    if len(units) != len(rul_at_end):
        raise ValueError(f"label count mismatch: {len(rul_at_end)} labels for {len(units)} units")

    end_rul = pd.Series(rul_at_end, index=units, name="end_rul")
    max_cycle = result.groupby("unit_number")["time_in_cycles"].transform("max")
    mapped_end = result["unit_number"].map(end_rul).astype("int32")

    result["rul"] = (max_cycle - result["time_in_cycles"] + mapped_end).astype("int32")
    result["rul_capped"] = result["rul"].clip(upper=cap).astype("int32")
    result["is_last_cycle"] = result["time_in_cycles"] == max_cycle
    return result


def load_subset(subset: Subset, raw_dir: Path, *, cap: int = R_EARLY) -> SubsetFrames:
    """Load and label both splits of one subset from the raw directory."""
    train = add_train_rul(read_raw(raw_dir / f"train_{subset.value}.txt"), cap=cap)
    test = add_test_rul(
        read_raw(raw_dir / f"test_{subset.value}.txt"),
        read_rul_labels(raw_dir / f"RUL_{subset.value}.txt"),
        cap=cap,
    )
    return SubsetFrames(subset=subset, train=train, test=test)


def to_parquet(frames: SubsetFrames, out_dir: Path) -> tuple[Path, Path]:
    """Persist both splits as Parquet, returning the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / f"{frames.subset.value}_train.parquet"
    test_path = out_dir / f"{frames.subset.value}_test.parquet"
    frames.train.to_parquet(train_path, index=False, compression="zstd")
    frames.test.to_parquet(test_path, index=False, compression="zstd")
    return train_path, test_path


def convert_all(
    raw_dir: Path, out_dir: Path, *, verbose: bool = True
) -> dict[Subset, SubsetFrames]:
    """Parse every subset and write the Parquet interim layer."""
    result: dict[Subset, SubsetFrames] = {}
    for subset in Subset:
        frames = load_subset(subset, raw_dir)
        to_parquet(frames, out_dir)
        result[subset] = frames
        if verbose:
            print(
                f"  {subset.value}: train {len(frames.train):>7,} rows / "
                f"{frames.train_units:>3} units, "
                f"test {len(frames.test):>7,} rows / {frames.test_units:>3} units"
            )
    return result


def load_parquet(subset: Subset, split: Split, interim_dir: Path) -> pd.DataFrame:
    """Reload a previously converted split."""
    path = interim_dir / f"{subset.value}_{split}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found -- run `make data` first")
    return pd.read_parquet(path)
