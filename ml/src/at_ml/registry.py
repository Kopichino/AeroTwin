"""Model registry and export (Doc 07 section 7.8).

The registry is the only interface between training and serving. Training
produces a TorchScript artifact plus a manifest entry; the inference service
loads by ``model_id`` and never imports the training code. That keeps pandas,
matplotlib and the rest of the research stack out of the serving image.

Every entry records the artifact hash, the exact feature list, the window size
and the scaler, because a model served with a mismatched feature order fails
silently rather than loudly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import torch
from torch import nn

Stage = Literal["DEV", "STAGING", "PRODUCTION", "ARCHIVED"]


@dataclass(slots=True)
class ModelEntry:
    """One registered model version."""

    model_id: str
    name: str
    version: str
    architecture: str
    subset: str
    stage: Stage
    artifact_uri: str
    artifact_sha256: str
    window: int
    features: list[str]
    scaler: dict[str, Any]
    metrics: dict[str, Any]
    hyperparams: dict[str, Any]
    parameters: int
    train_seed: int
    trained_at: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Registry:
    """JSON-backed model registry."""

    path: Path
    entries: list[ModelEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Registry:
        if not path.is_file():
            return cls(path=path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            entries=[ModelEntry(**item) for item in payload.get("models", [])],
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "models": [entry.to_dict() for entry in self.entries],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, entry: ModelEntry) -> None:
        self.entries = [
            existing for existing in self.entries if existing.model_id != entry.model_id
        ]
        self.entries.append(entry)

    def production_for(self, subset: str) -> ModelEntry | None:
        """The single production model for a subset, if one is promoted."""
        for entry in self.entries:
            if entry.subset == subset and entry.stage == "PRODUCTION":
                return entry
        return None

    def promote(self, model_id: str, stage: Stage) -> ModelEntry:
        """Promote a model, enforcing one production model per subset.

        Demoting the incumbent automatically prevents the ambiguous state where
        two models claim to be production for the same subset -- which would make
        serving behaviour depend on list ordering.
        """
        target = next((e for e in self.entries if e.model_id == model_id), None)
        if target is None:
            raise KeyError(f"unknown model_id: {model_id}")

        if stage == "PRODUCTION":
            for entry in self.entries:
                if (
                    entry.subset == target.subset
                    and entry.stage == "PRODUCTION"
                    and entry.model_id != model_id
                ):
                    entry.stage = "ARCHIVED"

        target.stage = stage
        return target


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def export_torchscript(model: nn.Module, window: int, n_features: int, destination: Path) -> Path:
    """Trace the model to TorchScript.

    Tracing rather than scripting because these architectures are static graphs
    with no data-dependent control flow, and traced modules load without the
    original class definition being importable -- which is what decouples the
    serving image from the training package.
    """
    model.eval()
    destination.parent.mkdir(parents=True, exist_ok=True)
    example = torch.randn(2, window, n_features)

    with torch.inference_mode():
        traced = torch.jit.trace(model, example, strict=False)  # type: ignore[no-untyped-call]
        traced = torch.jit.freeze(traced)

    traced.save(str(destination))
    return destination


def verify_export(
    original: nn.Module, artifact: Path, window: int, n_features: int, tolerance: float = 1e-4
) -> float:
    """Assert the exported artifact reproduces the original model's output.

    A silent divergence between the trained and served model is among the worst
    failure modes in an ML system, so export is verified rather than trusted.
    Returns the maximum absolute difference.
    """
    original.eval()
    loaded = torch.jit.load(str(artifact))  # type: ignore[no-untyped-call]
    loaded.eval()

    example = torch.randn(8, window, n_features)
    with torch.inference_mode():
        expected = original(example)
        actual = loaded(example)

    difference = float((expected - actual).abs().max())
    if difference > tolerance:
        raise ValueError(
            f"exported artifact diverges from the source model by {difference:.2e} "
            f"(tolerance {tolerance:.0e})"
        )
    return difference


def register(
    result: Any,
    data: Any,
    *,
    registry_path: Path,
    artifacts_dir: Path,
    name: str = "rul",
    version: str = "v1.0.0",
    stage: Stage = "DEV",
    notes: str = "",
) -> ModelEntry:
    """Export, verify and register a trained model."""
    subset = data.subset.value
    model_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"aerotwin/{name}/{result.architecture}/{subset}/{version}")
    )

    artifact = artifacts_dir / model_id / "model.pt"
    export_torchscript(result.model, data.train.window, data.train.n_features, artifact)
    divergence = verify_export(result.model, artifact, data.train.window, data.train.n_features)

    entry = ModelEntry(
        model_id=model_id,
        name=f"{name}-{result.architecture}",
        version=version,
        architecture=result.architecture,
        subset=subset,
        stage=stage,
        artifact_uri=str(artifact),
        artifact_sha256=sha256_of(artifact),
        window=data.train.window,
        features=list(data.train.features),
        scaler=data.scaler.to_dict(),
        metrics={
            "test": result.test_metrics.to_dict(),
            "val": result.val_metrics.to_dict(),
            "export_divergence": divergence,
        },
        hyperparams={
            "epochs": result.config.epochs,
            "batch_size": result.config.batch_size,
            "learning_rate": result.config.learning_rate,
            "weight_decay": result.config.weight_decay,
            "huber_delta": result.config.huber_delta,
            "best_epoch": result.best_epoch,
        },
        parameters=result.parameters,
        train_seed=result.config.seed,
        trained_at=datetime.now(UTC).isoformat(),
        notes=notes,
    )

    registry = Registry.load(registry_path)
    registry.add(entry)
    registry.save()
    return entry


def write_model_card(entry: ModelEntry, destination: Path) -> Path:
    """Emit a model card alongside the artifact."""
    test = entry.metrics["test"]
    lines = [
        f"# Model card: {entry.name} {entry.version}\n",
        f"- **Model id**: `{entry.model_id}`",
        f"- **Architecture**: {entry.architecture} ({entry.parameters:,} parameters)",
        f"- **Subset**: {entry.subset} · window {entry.window} · {len(entry.features)} features",
        f"- **Stage**: {entry.stage}",
        f"- **Trained**: {entry.trained_at} (seed {entry.train_seed})",
        f"- **Artifact**: `{entry.artifact_uri}`",
        f"- **SHA-256**: `{entry.artifact_sha256[:16]}...`\n",
        "## Intended use\n",
        "Estimates Remaining Useful Life in flight cycles for a simulated turbofan "
        "from a window of gas-path sensor readings. Decision **support** for "
        "maintenance planning; it is not an airworthiness determination and must "
        "not be used as one.\n",
        "## Performance\n",
        "| metric | value |",
        "|---|---|",
        f"| RMSE | {test['rmse']:.3f} |",
        f"| NASA score | {test['nasa_score']:.1f} |",
        f"| MAE | {test['mae']:.3f} |",
        f"| R² | {test['r2']:.4f} |",
        f"| RMSE (RUL ≤ 25) | {test['rmse_critical']:.3f} |",
        f"| Late predictions | {test['late_fraction']:.1%} |\n",
        "## Limitations\n",
        f"- Trained solely on NASA C-MAPSS {entry.subset}, a **simulation**. "
        "Transfer to a physical engine is unvalidated.\n"
        "- Labels are capped at 125 cycles, so the model cannot distinguish a "
        "very healthy engine from a merely healthy one, by construction.\n"
        f"- {test['late_fraction']:.0%} of predictions overestimate remaining life. "
        "The NASA score penalises these asymmetrically for exactly this reason.\n"
        "- Accuracy degrades outside the operating regimes present in training; "
        "the regime classifier assigns unseen conditions to the nearest centroid.\n",
        "## Ethical and safety notes\n",
        "Predictions are advisory. Every maintenance work package derived from "
        "this model requires human approval before execution (Doc 09: the platform "
        "is deliberately Level-4 prescriptive, not Level-5 autonomous).\n",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
