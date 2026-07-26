"""Tests for model-backed inference, uncertainty and attribution (Doc 07 sections 7.5, 7.7)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import torch

from at_twin.inference import (
    BREAKER_THRESHOLD,
    FAILURE_HORIZONS,
    CircuitBreaker,
    InferenceClient,
    LoadedModel,
    _failure_probability,
    calibrate_conformal,
    load_production_models,
    predict_batch,
)

REGISTRY = Path("models/registry.json")
registered = pytest.mark.skipif(
    not REGISTRY.is_file(), reason="no models registered; run `make train`"
)


class ConstantModel(torch.nn.Module):
    """Deterministic stand-in so tests do not depend on a trained artifact."""

    def __init__(self, value: float = 50.0) -> None:
        super().__init__()
        self.value = value

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Depend on the input so gradients exist for attribution tests.
        return self.value + x.mean(dim=(1, 2)) * 0.0 + x[:, -1, 0] * 0.1


def fake_model(n_features: int = 4, window: int = 10) -> LoadedModel:
    return LoadedModel(
        model_id="test-model",
        subset="FD001",
        architecture="test",
        module=ConstantModel(),
        window=window,
        features=tuple(f"s{i}" for i in range(1, n_features + 1)),
        scaler_means={0: np.zeros(n_features, dtype=np.float32)},
        scaler_stds={0: np.ones(n_features, dtype=np.float32)},
        global_mean=np.zeros(n_features, dtype=np.float32),
        global_std=np.ones(n_features, dtype=np.float32),
    )


# ── circuit breaker ──────────────────────────────────────────────────────────


def test_breaker_starts_closed() -> None:
    assert not CircuitBreaker().is_open


def test_breaker_opens_after_repeated_failures() -> None:
    breaker = CircuitBreaker()
    for _ in range(BREAKER_THRESHOLD):
        breaker.record_failure()
    assert breaker.is_open


def test_a_single_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker()
    for _ in range(BREAKER_THRESHOLD - 1):
        breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert not breaker.is_open


def test_breaker_half_opens_after_the_reset_window() -> None:
    breaker = CircuitBreaker(threshold=1, reset_after=0.01)
    breaker.record_failure()
    assert breaker.is_open
    time.sleep(0.02)
    assert not breaker.is_open, "must allow a probe after the reset window"


# ── scaling ──────────────────────────────────────────────────────────────────


def test_scaler_applies_per_regime_statistics() -> None:
    model = fake_model(n_features=2)
    model.scaler_means = {
        0: np.array([10.0, 0.0], dtype=np.float32),
        1: np.array([100.0, 0.0], dtype=np.float32),
    }
    model.scaler_stds = {
        0: np.array([1.0, 1.0], dtype=np.float32),
        1: np.array([1.0, 1.0], dtype=np.float32),
    }
    values = np.array([[[10.0, 0.0]], [[100.0, 0.0]]], dtype=np.float32)
    scaled = model.scale(values, np.array([0, 1]))
    assert scaled[0, 0, 0] == pytest.approx(0.0)
    assert scaled[1, 0, 0] == pytest.approx(0.0)


def test_unknown_regime_falls_back_to_global_statistics() -> None:
    model = fake_model(n_features=2)
    scaled = model.scale(np.zeros((1, 1, 2), dtype=np.float32), np.array([99]))
    assert np.isfinite(scaled).all()


# ── prediction ───────────────────────────────────────────────────────────────


def test_predict_returns_one_result_per_window() -> None:
    model = fake_model()
    results = predict_batch(model, np.random.randn(5, 10, 4).astype(np.float32), np.zeros(5, int))
    assert len(results) == 5


def test_interval_brackets_the_point_estimate() -> None:
    model = fake_model()
    result = predict_batch(model, np.zeros((1, 10, 4), dtype=np.float32), np.zeros(1, int))[0]
    assert result.rul_p10 <= result.rul_p50 <= result.rul_p90


def test_interval_is_never_degenerate() -> None:
    """A zero-width interval would imply certainty the model does not have."""
    model = fake_model()
    result = predict_batch(model, np.zeros((1, 10, 4), dtype=np.float32), np.zeros(1, int))[0]
    assert result.rul_p90 - result.rul_p10 > 0.0


def test_lower_bound_is_never_negative() -> None:
    model = fake_model()
    model.module = ConstantModel(value=1.0)
    result = predict_batch(model, np.zeros((1, 10, 4), dtype=np.float32), np.zeros(1, int))[0]
    assert result.rul_p10 >= 0.0


def test_failure_probabilities_cover_every_horizon() -> None:
    model = fake_model()
    result = predict_batch(model, np.zeros((1, 10, 4), dtype=np.float32), np.zeros(1, int))[0]
    assert set(result.failure_prob) == set(FAILURE_HORIZONS)
    assert all(0.0 <= p <= 1.0 for p in result.failure_prob.values())


def test_failure_probability_increases_with_horizon() -> None:
    assert (
        _failure_probability(50.0, 10.0, 30)
        < _failure_probability(50.0, 10.0, 60)
        < _failure_probability(50.0, 10.0, 90)
    )


def test_failure_probability_is_higher_for_a_sicker_engine() -> None:
    assert _failure_probability(10.0, 5.0, 30) > _failure_probability(100.0, 5.0, 30)


def test_zero_spread_gives_a_step_function() -> None:
    assert _failure_probability(20.0, 0.0, 30) == 1.0
    assert _failure_probability(50.0, 0.0, 30) == 0.0


def test_conformal_offset_widens_the_interval() -> None:
    model = fake_model()
    windows = np.zeros((1, 10, 4), dtype=np.float32)
    narrow = predict_batch(model, windows, np.zeros(1, int))[0]
    model.conformal_offset = 10.0
    wide = predict_batch(model, windows, np.zeros(1, int))[0]
    assert (wide.rul_p90 - wide.rul_p10) > (narrow.rul_p90 - narrow.rul_p10)


def test_conformal_calibration_returns_a_non_negative_offset() -> None:
    model = fake_model()
    windows = np.random.randn(40, 10, 4).astype(np.float32)
    truth = np.full(40, 80.0)  # model predicts ~50, so residuals are large
    assert calibrate_conformal(model, windows, np.zeros(40, int), truth) > 0.0


# ── attribution ──────────────────────────────────────────────────────────────


def test_attribution_is_absent_unless_requested() -> None:
    model = fake_model()
    result = predict_batch(model, np.random.randn(1, 10, 4).astype(np.float32), np.zeros(1, int))[0]
    assert result.attributions == ()


def test_attribution_names_real_sensors() -> None:
    model = fake_model()
    result = predict_batch(
        model,
        np.random.randn(1, 10, 4).astype(np.float32),
        np.zeros(1, int),
        explain=True,
    )[0]
    assert result.attributions
    for attribution in result.attributions:
        assert attribution.direction in {"up", "down"}
        assert attribution.name
        assert attribution.module


def test_attribution_maps_onto_engine_modules() -> None:
    model = fake_model()
    result = predict_batch(
        model,
        np.random.randn(1, 10, 4).astype(np.float32),
        np.zeros(1, int),
        explain=True,
    )[0]
    assert result.module_scores


# ── client ───────────────────────────────────────────────────────────────────


def test_client_without_models_returns_nothing() -> None:
    client = InferenceClient()
    assert client.predict("FD001", ["a"], np.zeros((1, 10, 4), np.float32), np.zeros(1, int)) == {}
    assert not client.available


def test_client_returns_results_keyed_by_engine() -> None:
    client = InferenceClient(models={"FD001": fake_model()})
    results = client.predict(
        "FD001", ["a", "b"], np.zeros((2, 10, 4), np.float32), np.zeros(2, int)
    )
    assert set(results) == {"a", "b"}


def test_client_falls_back_to_last_good_on_failure() -> None:
    """A broken model must not stall the tick loop (Doc 11 SEQ-08)."""
    model = fake_model()
    client = InferenceClient(models={"FD001": model})
    client.predict("FD001", ["a"], np.zeros((1, 10, 4), np.float32), np.zeros(1, int))

    class Broken(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            raise RuntimeError("model exploded")

    model.module = Broken()
    results = client.predict("FD001", ["a"], np.zeros((1, 10, 4), np.float32), np.zeros(1, int))

    assert results["a"].stale is True
    assert client.failures == 1


def test_client_opens_the_breaker_after_repeated_failures() -> None:
    model = fake_model()

    class Broken(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            raise RuntimeError("boom")

    model.module = Broken()
    client = InferenceClient(models={"FD001": model})
    for _ in range(BREAKER_THRESHOLD):
        client.predict("FD001", ["a"], np.zeros((1, 10, 4), np.float32), np.zeros(1, int))
    assert client.breaker.is_open
    assert not client.available


def test_client_reports_statistics() -> None:
    client = InferenceClient(models={"FD001": fake_model()})
    client.predict("FD001", ["a"], np.zeros((1, 10, 4), np.float32), np.zeros(1, int))
    stats = client.stats()
    assert stats["calls"] == 1
    assert stats["models_loaded"] == 1


# ── real registered artifacts ────────────────────────────────────────────────


@registered
def test_production_models_load() -> None:
    models = load_production_models(REGISTRY)
    assert models, "expected at least one PRODUCTION model"
    for subset, model in models.items():
        assert model.window > 0
        assert model.features
        assert model.subset == subset


@registered
def test_registered_model_produces_finite_non_negative_rul() -> None:
    """Random input is far outside the training distribution, so the *value* is
    meaningless -- only the invariants are asserted here. Accuracy is measured
    against real trajectories in the M4 comparison report."""
    models = load_production_models(REGISTRY)
    for model in models.values():
        windows = np.random.randn(4, model.window, len(model.features)).astype(np.float32)
        for result in predict_batch(model, windows, np.zeros(4, int)):
            assert np.isfinite(result.rul_p50)
            assert result.rul_p50 >= 0.0, "softplus head must never emit negative life"
            assert result.model_id


@registered
def test_registered_models_carry_a_conformal_offset() -> None:
    """Without calibration the 80 % interval covered only 47 % of the time."""
    models = load_production_models(REGISTRY)
    assert any(model.conformal_offset > 0 for model in models.values())


@registered
def test_batched_inference_meets_the_latency_budget() -> None:
    """NFR-2: p95 under 15 ms per engine when batched."""
    models = load_production_models(REGISTRY)
    model = next(iter(models.values()))
    windows = np.random.randn(64, model.window, len(model.features)).astype(np.float32)
    regimes = np.zeros(64, dtype=int)

    predict_batch(model, windows, regimes)  # warm up
    started = time.perf_counter()
    predict_batch(model, windows, regimes)
    per_engine = (time.perf_counter() - started) * 1000.0 / 64

    assert per_engine < 15.0, f"{per_engine:.2f} ms per engine exceeds the budget"
