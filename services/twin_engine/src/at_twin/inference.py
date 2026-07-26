"""Model-backed RUL inference with uncertainty and attribution (Doc 07 sections 7.5, 7.7).

Loads TorchScript artifacts from the registry and scores batched windows. Three
things distinguish this from a bare ``model(x)`` call:

* **Calibrated uncertainty.** A bare point estimate is not actionable for
  maintenance planning. MC-dropout gives a predictive spread; a conformal offset
  fitted on held-out data corrects that spread so the stated interval actually
  covers at its nominal rate.
* **Attribution.** Every prediction carries the sensors that drove it, computed
  by gradient x input, so the UI and the diagnosis agent can explain *why*.
* **Graceful degradation.** A circuit breaker keeps the tick loop running when
  inference is unavailable; the twin falls back to its last good prediction and
  flags it stale rather than stalling or emitting nonsense.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch

from at_core.domain.enums import EngineModule
from at_core.domain.sensors import SENSOR_BY_KEY, attribute_to_modules

#: Monte-Carlo dropout samples.
#:
#: Set to 0 on the streaming path. TorchScript **tracing** folds dropout away, so
#: repeated forward passes on a traced artifact return identical values: 21x the
#: compute for zero extra information, measured at 21 ms per batch versus 1 ms.
#: The interval instead comes from the conformal offset fitted in calibration,
#: which is distribution-free and does not assume the model can self-report its
#: own uncertainty. Scripted (not traced) models can raise this.
MC_SAMPLES = 0

#: Horizons, in cycles, at which failure probability is reported.
FAILURE_HORIZONS = (30, 60, 90)

#: Consecutive failures before the breaker opens, and how long it stays open.
BREAKER_THRESHOLD = 5
BREAKER_RESET_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Attribution:
    """One sensor's contribution to a prediction."""

    sensor: str
    name: str
    value: float
    direction: str
    module: str


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """A single engine's prediction with uncertainty and explanation."""

    rul_p50: float
    rul_p10: float
    rul_p90: float
    failure_prob: dict[int, float]
    attributions: tuple[Attribution, ...]
    module_scores: dict[EngineModule, float]
    model_id: str
    latency_ms: float
    stale: bool = False


class CircuitBreaker:
    """Trips after repeated failures, then probes for recovery.

    Without this, a broken model turns every tick into a failed call and the
    whole fleet stops advancing. Doc 11 SEQ-08.
    """

    def __init__(
        self, threshold: int = BREAKER_THRESHOLD, reset_after: float = BREAKER_RESET_SECONDS
    ) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.perf_counter() - self.opened_at >= self.reset_after:
            # Half-open: allow one probe through.
            self.opened_at = None
            self.failures = 0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.perf_counter()


@dataclass(slots=True)
class LoadedModel:
    """A TorchScript artifact plus everything needed to feed it correctly."""

    model_id: str
    subset: str
    architecture: str
    module: Any
    window: int
    features: tuple[str, ...]
    scaler_means: dict[int, np.ndarray]
    scaler_stds: dict[int, np.ndarray]
    global_mean: np.ndarray
    global_std: np.ndarray
    conformal_offset: float = 0.0
    """Additive half-width correction so the 80 % interval covers at 80 %."""

    def scale(self, values: np.ndarray, regimes: np.ndarray) -> np.ndarray:
        """Apply the persisted per-regime standardisation.

        Serving must reproduce training normalisation exactly; a mismatch here
        produces confidently wrong predictions with no error anywhere.
        """
        out = np.empty_like(values, dtype=np.float32)
        for index in range(values.shape[0]):
            regime = int(regimes[index])
            mean = self.scaler_means.get(regime, self.global_mean)
            std = self.scaler_stds.get(regime, self.global_std)
            out[index] = (values[index] - mean) / std
        return out


def load_production_models(registry_path: Path) -> dict[str, LoadedModel]:
    """Load every PRODUCTION artifact, keyed by subset."""
    if not registry_path.is_file():
        return {}

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    loaded: dict[str, LoadedModel] = {}

    for record in payload.get("models", []):
        if record.get("stage") != "PRODUCTION":
            continue
        artifact = Path(record["artifact_uri"])
        if not artifact.is_file():
            continue

        module = torch.jit.load(str(artifact))  # type: ignore[no-untyped-call]
        module.eval()
        scaler = record.get("scaler", {})

        loaded[record["subset"]] = LoadedModel(
            model_id=record["model_id"],
            subset=record["subset"],
            architecture=record["architecture"],
            module=module,
            window=record["window"],
            features=tuple(record["features"]),
            scaler_means={
                int(k): np.asarray(v, dtype=np.float32) for k, v in scaler.get("means", {}).items()
            },
            scaler_stds={
                int(k): np.asarray(v, dtype=np.float32) for k, v in scaler.get("stds", {}).items()
            },
            global_mean=np.asarray(scaler.get("global_mean", []), dtype=np.float32),
            global_std=np.asarray(scaler.get("global_std", []), dtype=np.float32),
            conformal_offset=float(record.get("conformal_offset", 0.0)),
        )

    return loaded


def _enable_dropout(module: Any) -> None:
    """Put dropout layers in train mode while everything else stays in eval.

    This is what makes MC-dropout work: batch-norm and the rest must stay
    deterministic, only the stochastic regularisers should sample.
    """
    for submodule in module.modules():
        if "dropout" in type(submodule).__name__.lower():
            submodule.train()


def predict_batch(
    loaded: LoadedModel,
    windows: np.ndarray,
    regimes: np.ndarray,
    *,
    mc_samples: int = MC_SAMPLES,
    explain: bool = False,
) -> list[InferenceResult]:
    """Score a batch of windows, with uncertainty and optional attribution.

    Args:
        windows: ``(B, W, F)`` raw (unscaled) feature windows.
        regimes: ``(B,)`` operating regime per window.
        explain: Compute gradient-based attributions. Roughly doubles cost, so
            the caller does this periodically rather than every cycle.
    """
    started = time.perf_counter()
    scaled = loaded.scale(windows.astype(np.float32), regimes)
    tensor = torch.from_numpy(scaled)

    with torch.inference_mode():
        point = loaded.module(tensor).cpu().numpy()

    # ── uncertainty ──────────────────────────────────────────────────────────
    spread = np.zeros_like(point)
    if mc_samples > 1:
        _enable_dropout(loaded.module)
        try:
            with torch.inference_mode():
                samples = np.stack([loaded.module(tensor).cpu().numpy() for _ in range(mc_samples)])
            spread = samples.std(axis=0)
        finally:
            loaded.module.eval()

    # A traced module may have folded dropout away, leaving zero spread. Fall
    # back to a proportional heuristic so the interval is never degenerate --
    # reporting a zero-width interval would imply certainty the model lacks.
    floor = np.maximum(0.08 * point, 2.0)
    spread = np.maximum(spread, floor) + loaded.conformal_offset

    # 80 % interval: +/- 1.2816 sigma under a normal approximation.
    z80 = 1.2816
    lower = np.maximum(0.0, point - z80 * spread)
    upper = point + z80 * spread

    # ── attribution ──────────────────────────────────────────────────────────
    attributions_per_row: list[tuple[Attribution, ...]] = [() for _ in range(len(point))]
    module_scores_per_row: list[dict[EngineModule, float]] = [{} for _ in range(len(point))]

    if explain:
        attributions_per_row, module_scores_per_row = _attribute(loaded, tensor)

    latency_ms = (time.perf_counter() - started) * 1000.0
    per_row_latency = latency_ms / max(1, len(point))

    results: list[InferenceResult] = []
    for index in range(len(point)):
        median = float(point[index])
        sigma = float(spread[index])
        results.append(
            InferenceResult(
                rul_p50=median,
                rul_p10=float(lower[index]),
                rul_p90=float(upper[index]),
                failure_prob={
                    horizon: _failure_probability(median, sigma, horizon)
                    for horizon in FAILURE_HORIZONS
                },
                attributions=attributions_per_row[index],
                module_scores=module_scores_per_row[index],
                model_id=loaded.model_id,
                latency_ms=per_row_latency,
            )
        )
    return results


def _failure_probability(median: float, sigma: float, horizon: int) -> float:
    """P(RUL < horizon) under a normal predictive distribution."""
    import math

    if sigma <= 1e-9:
        return 1.0 if median < horizon else 0.0
    z = (horizon - median) / sigma
    return float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))


def _attribute(
    loaded: LoadedModel, tensor: torch.Tensor
) -> tuple[list[tuple[Attribution, ...]], list[dict[EngineModule, float]]]:
    """Gradient x input attribution over the window.

    Chosen over full Integrated Gradients for the online path: IG needs ~50
    forward/backward passes per prediction, which does not fit a 260-engine tick
    budget. Gradient x input is a single backward pass and preserves the ranking
    that the UI and the diagnosis agent actually consume. Full IG and SHAP remain
    available for the on-demand "explain deeply" path.
    """
    grad_input = tensor.clone().requires_grad_(True)
    output = loaded.module(grad_input)
    output.sum().backward()

    gradients = grad_input.grad
    if gradients is None:  # pragma: no cover - defensive
        return [() for _ in range(tensor.shape[0])], [{} for _ in range(tensor.shape[0])]

    # Sum |grad * input| over the time axis to get per-feature importance.
    saliency = (gradients * grad_input).abs().sum(dim=1).detach().cpu().numpy()
    signed = (gradients * grad_input).sum(dim=1).detach().cpu().numpy()

    attributions: list[tuple[Attribution, ...]] = []
    module_scores: list[dict[EngineModule, float]] = []

    for row in range(saliency.shape[0]):
        magnitudes = saliency[row]
        total = float(magnitudes.sum()) or 1.0

        ranked = np.argsort(-magnitudes)[:8]
        row_attributions: list[Attribution] = []
        sensor_weights: dict[str, float] = {}

        for index in ranked:
            feature = loaded.features[index]
            # Engineered features (s3_d, s3_m5) attribute to their base sensor.
            base = feature.split("_")[0]
            spec = SENSOR_BY_KEY.get(base)
            if spec is None:
                continue
            weight = float(magnitudes[index]) / total
            sensor_weights[base] = sensor_weights.get(base, 0.0) + weight
            row_attributions.append(
                Attribution(
                    sensor=feature,
                    name=spec.symbol,
                    value=round(weight, 4),
                    direction="up" if signed[row][index] > 0 else "down",
                    module=spec.primary_module.value,
                )
            )

        attributions.append(tuple(row_attributions))
        module_scores.append(attribute_to_modules(sensor_weights))

    return attributions, module_scores


def calibrate_conformal(
    loaded: LoadedModel,
    windows: np.ndarray,
    regimes: np.ndarray,
    truth: np.ndarray,
    *,
    coverage: float = 0.8,
) -> float:
    """Fit the conformal offset that makes the interval cover at its nominal rate.

    Split-conformal: the offset is the ``coverage`` quantile of held-out absolute
    residuals, minus the model's own predicted spread. This is distribution-free
    and requires no assumption about the error being Gaussian, which matters
    because MC-dropout systematically understates uncertainty.
    """
    results = predict_batch(loaded, windows, regimes, mc_samples=MC_SAMPLES)
    predicted = np.array([r.rul_p50 for r in results])
    half_widths = np.array([(r.rul_p90 - r.rul_p10) / 2.0 for r in results])

    residuals = np.abs(truth - predicted)
    required = float(np.quantile(residuals, coverage))
    current = float(np.mean(half_widths))
    return max(0.0, (required - current) / 1.2816)


@dataclass(slots=True)
class InferenceClient:
    """Batched inference with a circuit breaker and last-good fallback."""

    models: dict[str, LoadedModel] = field(default_factory=dict)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    last_good: dict[str, InferenceResult] = field(default_factory=dict)
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    calls: int = 0
    failures: int = 0

    @property
    def available(self) -> bool:
        return bool(self.models) and not self.breaker.is_open

    def model_for(self, subset: str) -> LoadedModel | None:
        return self.models.get(subset)

    def predict(
        self,
        subset: str,
        engine_ids: list[str],
        windows: np.ndarray,
        regimes: np.ndarray,
        *,
        explain: bool = False,
    ) -> dict[str, InferenceResult]:
        """Score a batch, returning results keyed by engine id.

        On failure the breaker records it and each engine falls back to its last
        good prediction marked ``stale``, so the tick loop never blocks and the
        UI can show honestly that the number is out of date.
        """
        loaded = self.models.get(subset)
        if loaded is None or self.breaker.is_open:
            return self._fallback(engine_ids)

        try:
            started = time.perf_counter()
            results = predict_batch(loaded, windows, regimes, explain=explain)
            self.latencies.append((time.perf_counter() - started) * 1000.0)
            self.calls += 1
            self.breaker.record_success()
        except Exception:
            self.failures += 1
            self.breaker.record_failure()
            return self._fallback(engine_ids)

        output = dict(zip(engine_ids, results, strict=True))
        self.last_good.update(output)
        return output

    def _fallback(self, engine_ids: list[str]) -> dict[str, InferenceResult]:
        from dataclasses import replace as dc_replace

        output: dict[str, InferenceResult] = {}
        for engine_id in engine_ids:
            previous = self.last_good.get(engine_id)
            if previous is not None:
                output[engine_id] = dc_replace(previous, stale=True)
        return output

    def stats(self) -> dict[str, Any]:
        latencies = sorted(self.latencies)
        return {
            "models_loaded": len(self.models),
            "calls": self.calls,
            "failures": self.failures,
            "breaker_open": self.breaker.is_open,
            "latency_p50_ms": round(latencies[len(latencies) // 2], 3) if latencies else 0.0,
            "latency_p95_ms": (
                round(latencies[int(len(latencies) * 0.95)], 3) if latencies else 0.0
            ),
        }
