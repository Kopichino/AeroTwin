"""Tests for C-MAPSS parsing and RUL labelling.

Uses synthetic fixtures so the suite runs in CI without the dataset. Tests that
require the real files are marked ``dataset`` and skipped when it is absent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from at_data.parse import (
    ALL_COLUMNS,
    R_EARLY,
    add_test_rul,
    add_train_rul,
    load_subset,
    read_raw,
    read_rul_labels,
    to_parquet,
)

from at_core.domain.enums import Subset

RAW_DIR = Path("data/raw/cmapss")
dataset = pytest.mark.skipif(
    not (RAW_DIR / "train_FD001.txt").is_file(),
    reason="C-MAPSS dataset not present; run `make data`",
)


def write_raw(path: Path, rows: list[list[float]]) -> Path:
    """Write rows in the raw C-MAPSS whitespace format."""
    path.write_text("\n".join(" ".join(f"{v:g}" for v in row) for row in rows) + "\n")
    return path


def synthetic_rows(unit: int, cycles: int) -> list[list[float]]:
    return [
        [unit, cycle, 0.0, 0.0, 100.0, *[500.0 + cycle * 0.1] * 21]
        for cycle in range(1, cycles + 1)
    ]


# ── parsing ──────────────────────────────────────────────────────────────────


def test_read_raw_assigns_all_26_columns(tmp_path: Path) -> None:
    path = write_raw(tmp_path / "t.txt", synthetic_rows(1, 3))
    frame = read_raw(path)
    assert list(frame.columns) == list(ALL_COLUMNS)
    assert len(frame) == 3


def test_read_raw_uses_compact_dtypes(tmp_path: Path) -> None:
    """float32/int32 halve memory across 245k rows and match the DB schema."""
    frame = read_raw(write_raw(tmp_path / "t.txt", synthetic_rows(1, 2)))
    assert frame["unit_number"].dtype == np.int32
    assert frame["s1"].dtype == np.float32


def test_read_raw_tolerates_trailing_whitespace_columns(tmp_path: Path) -> None:
    """Some C-MAPSS releases have trailing spaces yielding phantom NaN columns."""
    path = tmp_path / "t.txt"
    path.write_text("1 1 0 0 100 " + " ".join(["500"] * 21) + "   \n")
    assert len(read_raw(path).columns) == 26


def test_read_raw_rejects_wrong_column_count(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("1 2 3\n")
    with pytest.raises(ValueError, match="expected 26 columns"):
        read_raw(path)


# ── training labels ──────────────────────────────────────────────────────────


def test_train_rul_counts_down_to_zero_at_failure() -> None:
    frame = pd.DataFrame(synthetic_rows(1, 5), columns=list(ALL_COLUMNS))
    result = add_train_rul(frame)
    assert result["rul"].tolist() == [4, 3, 2, 1, 0]


def test_train_rul_is_capped_at_r_early() -> None:
    frame = pd.DataFrame(synthetic_rows(1, 200), columns=list(ALL_COLUMNS))
    result = add_train_rul(frame)
    assert result["rul"].max() == 199
    assert result["rul_capped"].max() == R_EARLY


def test_train_rul_is_computed_per_unit() -> None:
    rows = synthetic_rows(1, 3) + synthetic_rows(2, 5)
    result = add_train_rul(pd.DataFrame(rows, columns=list(ALL_COLUMNS)))
    assert result[result.unit_number == 1]["rul"].tolist() == [2, 1, 0]
    assert result[result.unit_number == 2]["rul"].tolist() == [4, 3, 2, 1, 0]


# ── test labels ──────────────────────────────────────────────────────────────


def test_test_rul_adds_remaining_life_from_the_label_file() -> None:
    """Test trajectories stop before failure, so RUL never reaches zero."""
    frame = pd.DataFrame(synthetic_rows(1, 4), columns=list(ALL_COLUMNS))
    result = add_test_rul(frame, np.array([10]))
    assert result["rul"].tolist() == [13, 12, 11, 10]
    assert result["is_last_cycle"].tolist() == [False, False, False, True]


def test_test_rul_maps_labels_by_sorted_unit_order() -> None:
    rows = synthetic_rows(2, 2) + synthetic_rows(1, 2)
    result = add_test_rul(pd.DataFrame(rows, columns=list(ALL_COLUMNS)), np.array([5, 50]))
    assert result[result.unit_number == 1]["rul"].tolist() == [6, 5]
    assert result[result.unit_number == 2]["rul"].tolist() == [51, 50]


def test_test_rul_rejects_label_count_mismatch() -> None:
    frame = pd.DataFrame(synthetic_rows(1, 3), columns=list(ALL_COLUMNS))
    with pytest.raises(ValueError, match="label count mismatch"):
        add_test_rul(frame, np.array([1, 2, 3]))


def test_read_rul_labels(tmp_path: Path) -> None:
    path = tmp_path / "RUL.txt"
    path.write_text("112\n98\n69\n")
    assert read_rul_labels(path).tolist() == [112, 98, 69]


# ── round trip ───────────────────────────────────────────────────────────────


def test_parquet_round_trip_preserves_data(tmp_path: Path) -> None:
    from at_data.parse import SubsetFrames

    train = add_train_rul(pd.DataFrame(synthetic_rows(1, 10), columns=list(ALL_COLUMNS)))
    test = add_test_rul(
        pd.DataFrame(synthetic_rows(1, 5), columns=list(ALL_COLUMNS)), np.array([20])
    )
    frames = SubsetFrames(Subset.FD001, train, test)
    train_path, _ = to_parquet(frames, tmp_path)
    reloaded = pd.read_parquet(train_path)
    pd.testing.assert_frame_equal(reloaded, train)


# ── against the real dataset ─────────────────────────────────────────────────


@dataset
def test_fd001_unit_1_matches_official_rul_label() -> None:
    """End-to-end check of labelling against NASA's own ground truth."""
    frames = load_subset(Subset.FD001, RAW_DIR)
    unit_1 = frames.test[frames.test.unit_number == 1]
    official = read_rul_labels(RAW_DIR / "RUL_FD001.txt")[0]
    assert int(unit_1.iloc[-1]["rul"]) == int(official) == 112


@dataset
@pytest.mark.parametrize(
    ("subset", "train_rows", "test_rows"),
    [
        (Subset.FD001, 20_631, 13_096),
        (Subset.FD002, 53_759, 33_991),
        (Subset.FD003, 24_720, 16_596),
        (Subset.FD004, 61_249, 41_214),
    ],
)
def test_row_counts_match_the_official_release(
    subset: Subset, train_rows: int, test_rows: int
) -> None:
    frames = load_subset(subset, RAW_DIR)
    assert len(frames.train) == train_rows
    assert len(frames.test) == test_rows


@dataset
def test_every_test_trajectory_fits_its_window() -> None:
    """ADR-013: the window must never exceed the shortest test trajectory."""
    for subset in Subset:
        frames = load_subset(subset, RAW_DIR)
        shortest = frames.test.groupby("unit_number").size().min()
        assert subset.window_size <= shortest, (
            f"{subset.value}: window {subset.window_size} > shortest test trajectory {shortest}"
        )
