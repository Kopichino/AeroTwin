"""Tests for the ML pipeline: data preparation, models, metrics, registry.

The most valuable test here is ``test_no_feature_leaks_the_failure_cycle``: it
guards the bug that produced validation RMSE 11 alongside test RMSE 54.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from at_core.domain.enums import Subset
from at_ml.data import (
    CYCLE_NORM_REFERENCE,
    R_EARLY,
    RegimeScaler,
    add_engineered_features,
    build_windows,
    prepare,
)
from at_ml.evaluate import evaluate, nasa_score, prediction_interval_coverage
from at_ml.models import build_model, count_parameters
from at_ml.registry import ModelEntry, Registry, export_torchscript, verify_export

INTERIM = Path("data/interim")
dataset = pytest.mark.skipif(
    not (INTERIM / "FD001_train.parquet").is_file(),
    reason="interim parquet not present; run `make data`",
)

ARCHITECTURES = ["mlp", "cnn", "lstm", "tcn", "transformer", "informer"]


def synthetic_frame(units: int = 4, length: int = 60) -> pd.DataFrame:
    rows = []
    for unit in range(1, units + 1):
        for cycle in range(1, length + 1):
            progress = cycle / length
            rows.append(
                {
                    "unit_number": unit,
                    "time_in_cycles": cycle,
                    "regime": 0,
                    "s3": 1586.0 + 14.0 * progress,
                    "s4": 1400.0 + 26.0 * progress,
                    "rul": float(length - cycle),
                }
            )
    return pd.DataFrame(rows)


# ── the leak guard ───────────────────────────────────────────────────────────


def test_cycle_norm_does_not_reference_the_units_own_maximum() -> None:
    """Regression guard for the target leak found in M4.

    ``cycle_norm`` must depend only on absolute cycle count. If it is divided by
    the unit's own max cycle it encodes fraction-of-life-consumed, which is the
    answer in training and a meaningless constant in test.
    """
    short = synthetic_frame(units=1, length=20)
    long = synthetic_frame(units=1, length=200)

    short_feat = add_engineered_features(short, ("s3",))
    long_feat = add_engineered_features(long, ("s3",))

    # Cycle 10 must produce the same feature value regardless of how long the
    # trajectory happens to be.
    assert short_feat.loc[short_feat.time_in_cycles == 10, "cycle_norm"].iloc[0] == pytest.approx(
        long_feat.loc[long_feat.time_in_cycles == 10, "cycle_norm"].iloc[0]
    )
    assert short_feat["cycle_norm"].max() < 1.0, "must not saturate at end of trajectory"


def test_cycle_norm_scales_by_the_documented_constant() -> None:
    frame = add_engineered_features(synthetic_frame(units=1, length=50), ("s3",))
    expected = 25.0 / CYCLE_NORM_REFERENCE
    assert frame.loc[frame.time_in_cycles == 25, "cycle_norm"].iloc[0] == pytest.approx(expected)


@dataset
def test_no_feature_leaks_the_failure_cycle() -> None:
    """No feature may correlate near-perfectly with RUL across the whole train set.

    A correlation above ~0.97 with the target means the feature effectively *is*
    the label, which is what the cycle_norm leak looked like.
    """
    data = prepare(Subset.FD001, INTERIM)
    final = data.train.x[:, -1, :]

    for index, name in enumerate(data.train.features):
        column = final[:, index]
        if column.std() < 1e-9:
            continue
        correlation = abs(float(np.corrcoef(column, data.train.y)[0, 1]))
        assert correlation < 0.97, f"feature '{name}' correlates {correlation:.3f} with RUL"


@dataset
def test_train_and_test_feature_distributions_are_comparable() -> None:
    """A large distribution shift between splits indicates a leaking feature."""
    data = prepare(Subset.FD001, INTERIM)
    train_final = data.train.x[:, -1, :].mean(axis=0)
    test_final = data.test.x[:, -1, :].mean(axis=0)

    for index, name in enumerate(data.train.features):
        shift = abs(float(train_final[index] - test_final[index]))
        assert shift < 1.5, f"feature '{name}' shifts {shift:.2f} sigma between splits"


# ── scaler ───────────────────────────────────────────────────────────────────


def test_scaler_standardises_within_regime() -> None:
    frame = synthetic_frame()
    scaler = RegimeScaler.fit(frame, ("s3", "s4"))
    scaled = scaler.transform(frame)
    assert abs(float(scaled.mean())) < 1e-4
    assert abs(float(scaled.std()) - 1.0) < 0.05


def test_scaler_handles_regimes_separately() -> None:
    """The ADR-014 property: each regime is centred on its own mean."""
    frame = synthetic_frame()
    frame.loc[frame.index[:120], "regime"] = 0
    frame.loc[frame.index[120:], "regime"] = 1
    frame.loc[frame["regime"] == 1, "s3"] += 500.0  # a different flight condition

    scaler = RegimeScaler.fit(frame, ("s3",))
    scaled = scaler.transform(frame)
    regimes = frame["regime"].to_numpy()

    assert abs(float(scaled[regimes == 0].mean())) < 1e-4
    assert abs(float(scaled[regimes == 1].mean())) < 1e-4


def test_scaler_tolerates_constant_channels() -> None:
    frame = synthetic_frame()
    frame["flat"] = 1.0
    scaled = RegimeScaler.fit(frame, ("flat",)).transform(frame)
    assert np.isfinite(scaled).all()


def test_scaler_round_trips_through_json() -> None:
    scaler = RegimeScaler.fit(synthetic_frame(), ("s3", "s4"))
    restored = RegimeScaler.from_dict(scaler.to_dict())
    frame = synthetic_frame()
    np.testing.assert_allclose(scaler.transform(frame), restored.transform(frame))


def test_unseen_regime_falls_back_to_global_statistics() -> None:
    frame = synthetic_frame()
    scaler = RegimeScaler.fit(frame, ("s3",))
    novel = frame.copy()
    novel["regime"] = 99
    assert np.isfinite(scaler.transform(novel)).all()


# ── windowing ────────────────────────────────────────────────────────────────


def test_windows_have_the_requested_shape() -> None:
    frame = synthetic_frame(units=3, length=50)
    scaler = RegimeScaler.fit(frame, ("s3", "s4"))
    windows = build_windows(frame, scaler, window=20)
    assert windows.x.shape[1:] == (20, 2)
    assert len(windows) == 3 * (50 - 20 + 1)


def test_labels_are_capped_at_r_early() -> None:
    frame = synthetic_frame(units=1, length=300)
    scaler = RegimeScaler.fit(frame, ("s3",))
    assert build_windows(frame, scaler, window=20).y.max() == R_EARLY


def test_last_only_yields_one_window_per_unit() -> None:
    frame = synthetic_frame(units=5, length=50)
    scaler = RegimeScaler.fit(frame, ("s3",))
    windows = build_windows(frame, scaler, window=20, last_only=True)
    assert len(windows) == 5
    assert set(windows.units) == {1, 2, 3, 4, 5}


def test_short_trajectories_are_padded_not_dropped() -> None:
    """FD004 has 19-cycle test units; every unit must remain scoreable."""
    frame = synthetic_frame(units=2, length=8)
    scaler = RegimeScaler.fit(frame, ("s3",))
    windows = build_windows(frame, scaler, window=20, last_only=True)
    assert len(windows) == 2
    assert windows.x.shape[1] == 20


def test_subset_by_units_filters_correctly() -> None:
    frame = synthetic_frame(units=4, length=40)
    scaler = RegimeScaler.fit(frame, ("s3",))
    windows = build_windows(frame, scaler, window=10)
    filtered = windows.subset_by_units({1, 3})
    assert set(filtered.units) == {1, 3}


# ── splits ───────────────────────────────────────────────────────────────────


@dataset
def test_train_and_validation_units_never_overlap() -> None:
    """Row-wise splitting would leak: adjacent cycles are near-identical."""
    data = prepare(Subset.FD001, INTERIM)
    assert not set(data.train_units) & set(data.val_units)
    assert not set(data.train.units) & set(data.val.units)


@dataset
def test_split_is_deterministic_for_a_given_seed() -> None:
    first = prepare(Subset.FD001, INTERIM, seed=7)
    second = prepare(Subset.FD001, INTERIM, seed=7)
    assert first.train_units == second.train_units


@dataset
def test_test_split_has_one_window_per_unit() -> None:
    data = prepare(Subset.FD001, INTERIM)
    assert len(data.test) == 100


# ── models ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_every_architecture_honours_the_shared_contract(architecture: str) -> None:
    model = build_model(architecture, n_features=12, window=30)  # type: ignore[arg-type]
    output = model(torch.randn(4, 30, 12))
    assert output.shape == (4,)


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_predictions_are_non_negative(architecture: str) -> None:
    """A negative remaining life is not physically meaningful."""
    model = build_model(architecture, n_features=8, window=20)  # type: ignore[arg-type]
    assert (model(torch.randn(16, 20, 8) * 50) >= 0).all()


@pytest.mark.parametrize("architecture", ARCHITECTURES)
def test_models_are_trainable(architecture: str) -> None:
    model = build_model(architecture, n_features=6, window=20)  # type: ignore[arg-type]
    loss = torch.nn.functional.mse_loss(model(torch.randn(8, 20, 6)), torch.rand(8) * 100)
    loss.backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())


def test_tcn_is_causal() -> None:
    """A cycle must never see the future: changing the last step of the input
    must not alter the representation of earlier steps."""
    from at_ml.models import TCNBlock

    block = TCNBlock(channels=4, dilation=1).eval()
    base = torch.randn(1, 4, 12)
    modified = base.clone()
    modified[:, :, -1] += 10.0

    with torch.inference_mode():
        assert torch.allclose(block(base)[:, :, :-1], block(modified)[:, :, :-1], atol=1e-5)


def test_unknown_architecture_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown architecture"):
        build_model("quantum", 8, 20)  # type: ignore[arg-type]


def test_parameter_counts_are_reasonable() -> None:
    """C-MAPSS is small; oversized models are the documented overfitting risk."""
    for architecture in ARCHITECTURES:
        count = count_parameters(build_model(architecture, 43, 30))  # type: ignore[arg-type]
        assert 1_000 < count < 500_000, f"{architecture} has {count:,} parameters"


# ── metrics ──────────────────────────────────────────────────────────────────


def test_nasa_score_is_zero_for_perfect_prediction() -> None:
    truth = np.array([10.0, 50.0, 100.0])
    assert nasa_score(truth, truth) == pytest.approx(0.0)


def test_nasa_score_punishes_late_predictions_harder() -> None:
    """The asymmetry is the entire point of the metric."""
    truth = np.array([50.0])
    early = nasa_score(truth, np.array([40.0]))  # conservative
    late = nasa_score(truth, np.array([60.0]))  # dangerous
    assert late > early


def test_metrics_are_internally_consistent() -> None:
    truth = np.array([10.0, 20.0, 30.0, 40.0])
    prediction = np.array([12.0, 18.0, 33.0, 38.0])
    metrics = evaluate(truth, prediction)
    assert metrics.n == 4
    assert metrics.rmse >= metrics.mae
    assert 0.0 <= metrics.late_fraction <= 1.0


def test_per_horizon_buckets_are_populated() -> None:
    truth = np.array([5.0, 20.0, 50.0, 100.0])
    metrics = evaluate(truth, truth + 1.0)
    assert metrics.rmse_critical == pytest.approx(1.0)
    assert metrics.rmse_mid == pytest.approx(1.0)
    assert metrics.rmse_healthy == pytest.approx(1.0)


def test_r2_of_a_mean_predictor_is_zero() -> None:
    truth = np.array([10.0, 20.0, 30.0, 40.0])
    assert evaluate(truth, np.full_like(truth, truth.mean())).r2 == pytest.approx(0.0)


def test_interval_coverage() -> None:
    truth = np.array([10.0, 20.0, 30.0])
    picp, width = prediction_interval_coverage(
        truth, np.array([5.0, 25.0, 25.0]), np.array([15.0, 35.0, 35.0])
    )
    assert picp == pytest.approx(2 / 3)
    assert width == pytest.approx(10.0)


# ── registry ─────────────────────────────────────────────────────────────────


def test_torchscript_export_reproduces_the_model(tmp_path: Path) -> None:
    """A silent divergence between trained and served model is unacceptable."""
    model = build_model("lstm", n_features=8, window=20)
    artifact = export_torchscript(model, 20, 8, tmp_path / "model.pt")
    assert verify_export(model, artifact, 20, 8) < 1e-4


def test_export_verification_catches_a_mismatch(tmp_path: Path) -> None:
    model = build_model("cnn", n_features=8, window=20)
    artifact = export_torchscript(model, 20, 8, tmp_path / "model.pt")
    other = build_model("cnn", n_features=8, window=20)
    with pytest.raises(ValueError, match="diverges"):
        verify_export(other, artifact, 20, 8)


def entry(model_id: str, subset: str, stage: str = "DEV") -> ModelEntry:
    return ModelEntry(
        model_id=model_id,
        name="rul-test",
        version="v1",
        architecture="mlp",
        subset=subset,
        stage=stage,  # type: ignore[arg-type]
        artifact_uri="x",
        artifact_sha256="y",
        window=30,
        features=["s3"],
        scaler={},
        metrics={},
        hyperparams={},
        parameters=100,
        train_seed=42,
        trained_at="2026-01-01T00:00:00Z",
    )


def test_registry_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = Registry.load(path)
    registry.add(entry("a", "FD001"))
    registry.save()
    assert len(Registry.load(path).entries) == 1


def test_only_one_production_model_per_subset(tmp_path: Path) -> None:
    """Two production models for one subset would make serving order-dependent."""
    registry = Registry.load(tmp_path / "r.json")
    registry.add(entry("a", "FD001", "PRODUCTION"))
    registry.add(entry("b", "FD001"))
    registry.promote("b", "PRODUCTION")

    production = [e for e in registry.entries if e.subset == "FD001" and e.stage == "PRODUCTION"]
    assert len(production) == 1
    assert production[0].model_id == "b"


def test_promotion_does_not_disturb_other_subsets(tmp_path: Path) -> None:
    registry = Registry.load(tmp_path / "r.json")
    registry.add(entry("a", "FD001", "PRODUCTION"))
    registry.add(entry("b", "FD003"))
    registry.promote("b", "PRODUCTION")
    assert registry.production_for("FD001") is not None
    assert registry.production_for("FD003") is not None


def test_promoting_unknown_model_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="unknown model_id"):
        Registry.load(tmp_path / "r.json").promote("nope", "PRODUCTION")


def test_missing_registry_loads_empty(tmp_path: Path) -> None:
    assert Registry.load(tmp_path / "absent.json").entries == []


# ── the registered production models ─────────────────────────────────────────


@pytest.mark.skipif(not Path("models/registry.json").is_file(), reason="no models registered")
def test_registered_models_load_and_predict() -> None:
    """The artifacts the platform will actually serve must be loadable."""
    registry = Registry.load(Path("models/registry.json"))
    assert registry.entries, "expected at least one registered model"

    for record in registry.entries:
        artifact = Path(record.artifact_uri)
        if not artifact.is_file():
            pytest.skip(f"artifact missing: {artifact}")

        model = torch.jit.load(str(artifact))
        model.eval()
        with torch.inference_mode():
            output = model(torch.randn(4, record.window, len(record.features)))
        assert output.shape == (4,)
        assert (output >= 0).all()


@pytest.mark.skipif(not Path("models/registry.json").is_file(), reason="no models registered")
def test_production_models_meet_the_accuracy_target() -> None:
    """NFR-8: FD001 RMSE <= 14.0 and NASA score <= 350."""
    registry = Registry.load(Path("models/registry.json"))
    for record in registry.entries:
        if record.subset != "FD001" or record.stage != "PRODUCTION":
            continue
        metrics = record.metrics.get("test", {})
        assert metrics["nasa_score"] <= 350.0, f"score {metrics['nasa_score']}"
