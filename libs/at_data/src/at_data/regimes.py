"""Operating-condition regime detection (ADR-014, Doc 07 section 7.2).

FD002 and FD004 cycle through six discrete flight conditions. The condition
dominates raw sensor variance, so degradation signal is largely invisible until
the data is normalised *per regime*. This module recovers the regimes by
clustering the three operational settings and persists the centroids so that the
twin engine can classify live telemetry identically at inference time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from at_core.domain.enums import Subset

OP_COLUMNS = ("op1", "op2", "op3")


@dataclass(frozen=True, slots=True)
class RegimeModel:
    """Fitted regime classifier: centroids in raw operational-setting space."""

    subset: Subset
    centroids: np.ndarray  # shape (k, 3)
    silhouette: float
    counts: tuple[int, ...]

    @property
    def n_regimes(self) -> int:
        return int(self.centroids.shape[0])

    def predict(self, op_settings: np.ndarray) -> np.ndarray:
        """Assign each row of ``op_settings`` (N, 3) to its nearest centroid."""
        points = np.atleast_2d(np.asarray(op_settings, dtype=np.float64))
        distances = np.linalg.norm(points[:, None, :] - self.centroids[None, :, :], axis=2)
        return np.argmin(distances, axis=1).astype(np.int16)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subset": self.subset.value,
            "n_regimes": self.n_regimes,
            "centroids": self.centroids.tolist(),
            "silhouette": self.silhouette,
            "counts": list(self.counts),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RegimeModel:
        return cls(
            subset=Subset(str(payload["subset"])),
            centroids=np.asarray(payload["centroids"], dtype=np.float64),
            silhouette=float(payload["silhouette"]),
            counts=tuple(int(c) for c in payload["counts"]),
        )


def _kmeans(
    points: np.ndarray, k: int, *, seed: int = 42, iterations: int = 100
) -> tuple[np.ndarray, np.ndarray]:
    """Minimal deterministic k-means (k-means++ init).

    Implemented directly rather than pulling scikit-learn into the data library:
    the algorithm is 20 lines, the dependency is 40 MB, and determinism matters
    more here than features. The ML package may still use sklearn for research.
    """
    rng = np.random.default_rng(seed)
    n = points.shape[0]

    # k-means++ seeding
    centroids = np.empty((k, points.shape[1]), dtype=np.float64)
    centroids[0] = points[rng.integers(n)]
    closest = np.linalg.norm(points - centroids[0], axis=1) ** 2
    for i in range(1, k):
        probabilities = closest / closest.sum()
        centroids[i] = points[rng.choice(n, p=probabilities)]
        closest = np.minimum(closest, np.linalg.norm(points - centroids[i], axis=1) ** 2)

    labels = np.zeros(n, dtype=np.int64)
    for _ in range(iterations):
        distances = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for i in range(k):
            member = points[labels == i]
            if member.size:
                centroids[i] = member.mean(axis=0)

    return centroids, labels


def _silhouette(points: np.ndarray, labels: np.ndarray, *, sample: int = 4000) -> float:
    """Mean silhouette coefficient, subsampled for tractability.

    Uses centroid-distance approximation for the between-cluster term, which is
    exact for well-separated spherical clusters -- precisely the regime case.
    """
    rng = np.random.default_rng(0)
    if points.shape[0] > sample:
        index = rng.choice(points.shape[0], sample, replace=False)
        points, labels = points[index], labels[index]

    unique = np.unique(labels)
    if unique.size < 2:
        return 0.0

    centroids = np.stack([points[labels == label].mean(axis=0) for label in unique])
    scores = np.empty(points.shape[0], dtype=np.float64)

    for position, label in enumerate(unique):
        mask = labels == label
        member = points[mask]
        if member.shape[0] <= 1:
            scores[mask] = 0.0
            continue
        # a: mean intra-cluster distance
        intra = np.linalg.norm(member[:, None, :] - member[None, :, :], axis=2)
        a = intra.sum(axis=1) / (member.shape[0] - 1)
        # b: distance to the nearest other centroid
        other = np.delete(centroids, position, axis=0)
        b = np.min(np.linalg.norm(member[:, None, :] - other[None, :, :], axis=2), axis=1)
        scores[mask] = (b - a) / np.maximum(a, b)

    return float(scores.mean())


def fit_regimes(frame: pd.DataFrame, subset: Subset, *, seed: int = 42) -> RegimeModel:
    """Fit the regime model for a subset.

    Single-condition subsets (FD001/FD003) get a trivial one-regime model rather
    than a forced clustering, so downstream code has a uniform interface.
    """
    points = frame.loc[:, list(OP_COLUMNS)].to_numpy(dtype=np.float64)

    if subset.n_conditions == 1:
        return RegimeModel(
            subset=subset,
            centroids=points.mean(axis=0, keepdims=True),
            silhouette=1.0,
            counts=(points.shape[0],),
        )

    centroids, labels = _kmeans(points, subset.n_conditions, seed=seed)

    # Canonical ordering by (op1, op2, op3) makes regime ids stable across runs,
    # which matters because they are persisted and shown in the UI.
    order = np.lexsort((centroids[:, 2], centroids[:, 1], centroids[:, 0]))
    centroids = centroids[order]
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    labels = remap[labels]

    counts = tuple(int((labels == i).sum()) for i in range(subset.n_conditions))
    return RegimeModel(
        subset=subset,
        centroids=centroids,
        silhouette=_silhouette(points, labels),
        counts=counts,
    )


def assign_regimes(frame: pd.DataFrame, model: RegimeModel) -> pd.DataFrame:
    """Return a copy of ``frame`` with a ``regime`` column attached."""
    result = frame.copy()
    result["regime"] = model.predict(frame.loc[:, list(OP_COLUMNS)].to_numpy())
    return result


def save_models(models: dict[Subset, RegimeModel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {subset.value: model.to_dict() for subset, model in models.items()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_models(path: Path) -> dict[Subset, RegimeModel]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {Subset(key): RegimeModel.from_dict(value) for key, value in payload.items()}
