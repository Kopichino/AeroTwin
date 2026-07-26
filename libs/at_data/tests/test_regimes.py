"""Tests for operating-condition regime detection (ADR-014)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from at_data.parse import load_parquet
from at_data.regimes import (
    RegimeModel,
    assign_regimes,
    fit_regimes,
    load_models,
    save_models,
)

from at_core.domain.enums import Subset

INTERIM = Path("data/interim")
dataset = pytest.mark.skipif(
    not (INTERIM / "FD002_train.parquet").is_file(),
    reason="interim parquet not present; run `make data`",
)


def synthetic_regimes(n_per: int = 200) -> pd.DataFrame:
    """Six well-separated clusters resembling real flight conditions."""
    centres = [
        (0.0, 0.0, 100.0),
        (10.0, 0.25, 100.0),
        (20.0, 0.70, 100.0),
        (25.0, 0.62, 60.0),
        (35.0, 0.84, 100.0),
        (42.0, 0.84, 100.0),
    ]
    rng = np.random.default_rng(0)
    rows = [
        (c[0] + rng.normal(0, 0.01), c[1] + rng.normal(0, 0.001), c[2] + rng.normal(0, 0.01))
        for c in centres
        for _ in range(n_per)
    ]
    return pd.DataFrame(rows, columns=["op1", "op2", "op3"])


def test_single_condition_subset_gets_one_trivial_regime() -> None:
    frame = pd.DataFrame({"op1": [0.0, 0.001], "op2": [0.0, 0.0], "op3": [100.0, 100.0]})
    model = fit_regimes(frame, Subset.FD001)
    assert model.n_regimes == 1
    assert model.predict(np.array([[0.0, 0.0, 100.0]])).tolist() == [0]


def test_six_regimes_recovered_from_synthetic_conditions() -> None:
    model = fit_regimes(synthetic_regimes(), Subset.FD002)
    assert model.n_regimes == 6
    assert model.silhouette > 0.95
    assert all(count > 0 for count in model.counts)


def test_regime_ids_are_deterministic_across_runs() -> None:
    """Ids are persisted and displayed, so they must be stable, not seed-dependent."""
    frame = synthetic_regimes()
    first = fit_regimes(frame, Subset.FD002, seed=1)
    second = fit_regimes(frame, Subset.FD002, seed=999)
    np.testing.assert_allclose(first.centroids, second.centroids, atol=1e-6)


def test_centroids_are_sorted_canonically() -> None:
    """Canonical ordering by (op1, op2, op3) is what makes ids stable."""
    centroids = fit_regimes(synthetic_regimes(), Subset.FD002).centroids
    assert list(centroids[:, 0]) == sorted(centroids[:, 0])


def test_predict_assigns_points_to_the_nearest_centroid() -> None:
    model = fit_regimes(synthetic_regimes(), Subset.FD002)
    labels = model.predict(np.array([[0.0, 0.0, 100.0], [42.0, 0.84, 100.0]]))
    assert labels[0] == 0
    assert labels[1] == 5


def test_assign_regimes_adds_a_column() -> None:
    frame = synthetic_regimes()
    model = fit_regimes(frame, Subset.FD002)
    result = assign_regimes(frame, model)
    assert "regime" in result.columns
    assert set(result["regime"].unique()) == set(range(6))


def test_model_survives_a_json_round_trip(tmp_path: Path) -> None:
    """The twin engine loads these at boot; serialisation must be lossless."""
    original = fit_regimes(synthetic_regimes(), Subset.FD002)
    path = tmp_path / "regimes.json"
    save_models({Subset.FD002: original}, path)
    restored = load_models(path)[Subset.FD002]
    np.testing.assert_allclose(restored.centroids, original.centroids)
    assert restored.silhouette == original.silhouette


def test_round_trip_preserves_predictions(tmp_path: Path) -> None:
    frame = synthetic_regimes()
    original = fit_regimes(frame, Subset.FD002)
    path = tmp_path / "r.json"
    save_models({Subset.FD002: original}, path)
    restored = RegimeModel.from_dict(load_models(path)[Subset.FD002].to_dict())
    points = frame.to_numpy()
    np.testing.assert_array_equal(original.predict(points), restored.predict(points))


# ── against the real dataset ─────────────────────────────────────────────────


@dataset
@pytest.mark.parametrize("subset", [Subset.FD002, Subset.FD004])
def test_real_regimes_are_cleanly_separated(subset: Subset) -> None:
    """Doc 07 claims silhouette > 0.95. This proves it rather than asserting it."""
    model = fit_regimes(load_parquet(subset, "train", INTERIM), subset)
    assert model.n_regimes == 6
    assert model.silhouette > 0.95, f"{subset.value} silhouette {model.silhouette}"


@dataset
def test_per_regime_normalisation_reveals_degradation() -> None:
    """The empirical justification for ADR-014.

    Raw T30 barely correlates with RUL in FD002 because the flight condition
    dominates its variance. Normalising within regime recovers the signal.
    """
    frame = load_parquet(Subset.FD002, "train", INTERIM)
    model = fit_regimes(frame, Subset.FD002)
    frame = assign_regimes(frame, model)

    global_corr = abs(np.corrcoef(frame["s3"], frame["rul_capped"])[0, 1])
    z_scored = frame.groupby("regime")["s3"].transform(lambda x: (x - x.mean()) / x.std())
    regime_corr = abs(np.corrcoef(z_scored, frame["rul_capped"])[0, 1])

    assert global_corr < 0.10, "raw correlation should be negligible"
    assert regime_corr > 0.50, "per-regime correlation should be strong"
    assert regime_corr > global_corr * 5
