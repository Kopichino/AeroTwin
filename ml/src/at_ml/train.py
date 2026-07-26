"""Training loop for RUL models (Doc 07 section 7.3).

Deliberately small and readable rather than a framework. Every run is seeded,
early-stopped on validation NASA score, and returns a result object that the
comparison report and the model registry both consume.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, TensorDataset

from at_ml.data import WindowSet
from at_ml.evaluate import Metrics, evaluate
from at_ml.models import Architecture, build_model, count_parameters


def set_seed(seed: int) -> None:
    """Seed every source of randomness that affects a run."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


@dataclass(frozen=True, slots=True)
class TrainConfig:
    """Hyperparameters for one training run."""

    architecture: Architecture
    epochs: int = 40
    batch_size: int = 256
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 8
    huber_delta: float = 5.0
    """Huber loss transition point.

    More robust than MSE to the discontinuity introduced by the piecewise RUL
    cap, where many samples sit exactly at 125 and squared error over-penalises
    the resulting plateau.
    """
    warmup_epochs: int = 2
    grad_clip: float = 1.0
    seed: int = 42
    num_workers: int = 0


@dataclass(slots=True)
class TrainResult:
    """Outcome of a training run."""

    architecture: str
    config: TrainConfig
    model: nn.Module
    val_metrics: Metrics
    test_metrics: Metrics
    history: list[dict[str, float]] = field(default_factory=list)
    best_epoch: int = 0
    epochs_run: int = 0
    train_seconds: float = 0.0
    parameters: int = 0
    inference_ms_per_batch: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "architecture": self.architecture,
            "parameters": self.parameters,
            "best_epoch": self.best_epoch,
            "epochs_run": self.epochs_run,
            "train_seconds": round(self.train_seconds, 1),
            "val_rmse": round(self.val_metrics.rmse, 3),
            "val_score": round(self.val_metrics.nasa_score, 1),
            "test_rmse": round(self.test_metrics.rmse, 3),
            "test_score": round(self.test_metrics.nasa_score, 1),
            "test_mae": round(self.test_metrics.mae, 3),
            "test_r2": round(self.test_metrics.r2, 4),
            "rmse_critical": round(self.test_metrics.rmse_critical, 3),
            "late_fraction": round(self.test_metrics.late_fraction, 3),
            "inference_ms": round(self.inference_ms_per_batch, 2),
        }


def _loader(
    window_set: WindowSet, batch_size: int, *, shuffle: bool
) -> DataLoader[tuple[Tensor, ...]]:
    dataset = TensorDataset(torch.from_numpy(window_set.x), torch.from_numpy(window_set.y))
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, drop_last=False)


@torch.inference_mode()
def predict(model: nn.Module, window_set: WindowSet, batch_size: int = 512) -> np.ndarray:
    """Batched inference over a window set."""
    model.eval()
    outputs: list[Tensor] = []
    for (batch,) in DataLoader(
        TensorDataset(torch.from_numpy(window_set.x)), batch_size=batch_size
    ):
        outputs.append(model(batch))
    return torch.cat(outputs).cpu().numpy()


def train_model(
    config: TrainConfig,
    train_set: WindowSet,
    val_set: WindowSet,
    test_set: WindowSet,
    *,
    verbose: bool = True,
) -> TrainResult:
    """Train one architecture and evaluate it on validation and test splits.

    Model selection uses the **validation NASA score**, not RMSE: the score
    encodes the asymmetric cost of a late prediction, which is the property that
    actually matters operationally.
    """
    set_seed(config.seed)
    torch.set_num_threads(2)

    model = build_model(config.architecture, train_set.n_features, train_set.window)
    optimiser = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    criterion = nn.HuberLoss(delta=config.huber_delta)

    train_loader = _loader(train_set, config.batch_size, shuffle=True)
    steps_per_epoch = max(1, len(train_loader))
    total_steps = config.epochs * steps_per_epoch
    warmup_steps = config.warmup_epochs * steps_per_epoch

    def lr_scale(step: int) -> float:
        if step < warmup_steps:
            return (step + 1) / max(1, warmup_steps)
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return float(0.5 * (1.0 + np.cos(np.pi * min(1.0, progress))))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimiser, lr_scale)

    best_score = float("inf")
    best_state: dict[str, Tensor] = {}
    best_epoch = 0
    epochs_without_improvement = 0
    history: list[dict[str, float]] = []

    started = time.perf_counter()

    for epoch in range(1, config.epochs + 1):
        model.train()
        epoch_loss = 0.0
        for features, targets in train_loader:
            optimiser.zero_grad(set_to_none=True)
            loss = criterion(model(features), targets)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            optimiser.step()
            scheduler.step()
            epoch_loss += loss.detach().item()

        val_pred = predict(model, val_set)
        val_metrics = evaluate(val_set.y, val_pred)

        history.append(
            {
                "epoch": epoch,
                "loss": epoch_loss / steps_per_epoch,
                "val_rmse": val_metrics.rmse,
                "val_score": val_metrics.nasa_score,
            }
        )

        if val_metrics.nasa_score < best_score:
            best_score = val_metrics.nasa_score
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if verbose and (epoch % 5 == 0 or epoch == 1):
            print(
                f"    epoch {epoch:>3}  loss {epoch_loss / steps_per_epoch:7.3f}"
                f"  val_rmse {val_metrics.rmse:6.2f}  val_score {val_metrics.nasa_score:9.1f}"
            )

        if epochs_without_improvement >= config.patience:
            if verbose:
                print(f"    early stop at epoch {epoch} (best {best_epoch})")
            break

    elapsed = time.perf_counter() - started

    if best_state:
        model.load_state_dict(best_state)

    val_metrics = evaluate(val_set.y, predict(model, val_set))

    inference_started = time.perf_counter()
    test_pred = predict(model, test_set)
    inference_ms = (time.perf_counter() - inference_started) * 1000.0
    test_metrics = evaluate(test_set.y, test_pred)

    return TrainResult(
        architecture=config.architecture,
        config=config,
        model=model,
        val_metrics=val_metrics,
        test_metrics=test_metrics,
        history=history,
        best_epoch=best_epoch,
        epochs_run=len(history),
        train_seconds=elapsed,
        parameters=count_parameters(model),
        inference_ms_per_batch=inference_ms / max(1, len(test_set) / 512),
    )
