"""Model comparison harness (Doc 07 section 7.4).

Trains every architecture on a subset under identical conditions and emits the
comparison report. Fairness is the whole point, so all models share the same
data preparation, loss, optimiser family, schedule, early-stopping rule and
seed. Any difference in the table is attributable to architecture.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from at_core.domain.enums import Subset
from at_ml.data import PreparedData, prepare
from at_ml.evaluate import evaluate
from at_ml.models import Architecture
from at_ml.train import TrainConfig, TrainResult, predict, train_model

DEFAULT_ARCHITECTURES: tuple[Architecture, ...] = (
    "mlp",
    "cnn",
    "lstm",
    "tcn",
    "transformer",
    "informer",
)


@dataclass(slots=True)
class ComparisonRun:
    """All results for one subset."""

    subset: Subset
    results: dict[str, TrainResult]
    data: PreparedData
    ensemble: dict[str, Any] | None = None

    def best(self, metric: str = "nasa_score") -> str:
        """Architecture with the lowest test value of ``metric``."""
        return min(
            self.results,
            key=lambda name: getattr(self.results[name].test_metrics, metric),
        )


def run_comparison(
    subset: Subset,
    interim: Path,
    *,
    architectures: tuple[Architecture, ...] = DEFAULT_ARCHITECTURES,
    epochs: int = 30,
    seed: int = 42,
    verbose: bool = True,
) -> ComparisonRun:
    """Train every architecture on one subset under identical conditions."""
    data = prepare(subset, interim, seed=seed)
    results: dict[str, TrainResult] = {}

    for architecture in architectures:
        if verbose:
            print(f"\n  [{subset.value}] {architecture}")
        results[architecture] = train_model(
            TrainConfig(architecture=architecture, epochs=epochs, seed=seed),
            data.train,
            data.val,
            data.test,
            verbose=verbose,
        )
        if verbose:
            summary = results[architecture].summary()
            print(
                f"    -> test RMSE {summary['test_rmse']}  "
                f"score {summary['test_score']}  "
                f"R2 {summary['test_r2']}  ({summary['train_seconds']}s)"
            )

    run = ComparisonRun(subset=subset, results=results, data=data)
    run.ensemble = _build_ensemble(run)
    return run


def _build_ensemble(run: ComparisonRun) -> dict[str, Any] | None:
    """Average the top-3 architectures by validation NASA score.

    Selection uses validation only; the test set is never consulted when
    choosing members, which is what keeps the reported ensemble score honest.
    """
    if len(run.results) < 3:
        return None

    ranked = sorted(run.results.items(), key=lambda item: item[1].val_metrics.nasa_score)[:3]
    members = [name for name, _ in ranked]

    predictions = np.mean([predict(result.model, run.data.test) for _, result in ranked], axis=0)
    metrics = evaluate(run.data.test.y, predictions)

    return {
        "members": members,
        "metrics": metrics.to_dict(),
    }


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    """Render a markdown table with the best value in each column emboldened."""
    lines = ["| " + " | ".join(label for _, label in columns) + " |"]
    lines.append("|" + "|".join("---" for _ in columns) + "|")

    # Every column here is "lower is better" except R2. Getting this wrong made
    # the first report embolden the *worst* late-prediction rate as if it were
    # the best result.
    higher_is_better = {"test_r2"}
    best: dict[str, float] = {}
    for key, _ in columns:
        values = [r[key] for r in rows if isinstance(r.get(key), int | float)]
        if values:
            best[key] = max(values) if key in higher_is_better else min(values)

    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, int | float) and key in best and value == best[key]:
                cells.append(f"**{value}**")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(runs: list[ComparisonRun], output: Path) -> Path:
    """Write the model comparison report."""
    lines: list[str] = []
    lines.append("# RUL Model Comparison\n")
    lines.append(
        f"_Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by "
        "`at_ml.compare`. Regenerate with `make train`._\n"
    )
    lines.append(
        f"Hardware: {platform.processor() or platform.machine()}, CPU only. "
        "All models share identical data preparation, loss, optimiser, schedule, "
        "early-stopping rule and seed, so differences are attributable to "
        "architecture rather than tuning effort.\n"
    )

    lines.append("\n## Protocol\n")
    lines.append(
        "- **Labels**: piecewise-linear RUL capped at 125 cycles (ADR-012).\n"
        "- **Normalisation**: per-regime z-score, fitted on train only (ADR-014).\n"
        "- **Splits**: 80/20 by **unit**, never by row. Adjacent cycles of one "
        "engine are near-identical, so a row split leaks the answer.\n"
        "- **Test protocol**: final window per unit, scored against the official "
        "`RUL_FD00X.txt` labels.\n"
        "- **Model selection**: lowest *validation* NASA score, which encodes the "
        "asymmetric cost of predicting more life than an engine has.\n"
        "- **Loss**: Huber (delta 5), more robust than MSE to the plateau created "
        "by the label cap.\n"
    )

    for run in runs:
        subset = run.subset
        lines.append(f"\n## {subset.value}\n")
        lines.append(
            f"{len(run.data.train):,} train windows / {len(run.data.train_units)} units · "
            f"{len(run.data.val):,} val / {len(run.data.val_units)} units · "
            f"{len(run.data.test)} test units · "
            f"window {run.data.train.window} · {run.data.train.n_features} features\n"
        )

        rows = [result.summary() for result in run.results.values()]
        lines.append(
            _table(
                rows,
                [
                    ("architecture", "model"),
                    ("parameters", "params"),
                    ("test_rmse", "RMSE"),
                    ("test_score", "NASA score"),
                    ("test_mae", "MAE"),
                    ("test_r2", "R²"),
                    ("rmse_critical", "RMSE (RUL≤25)"),
                    ("late_fraction", "late %"),
                    ("train_seconds", "train s"),
                ],
            )
        )

        if run.ensemble:
            metrics = run.ensemble["metrics"]
            lines.append(
                f"\n**Ensemble** (mean of {', '.join(run.ensemble['members'])}, "
                f"selected on validation): RMSE **{metrics['rmse']:.3f}**, "
                f"NASA score **{metrics['nasa_score']:.1f}**, "
                f"R² {metrics['r2']:.4f}\n"
            )

        best_rmse = run.best("rmse")
        best_score = run.best("nasa_score")
        lines.append(f"\nBest RMSE: `{best_rmse}`. Best NASA score: `{best_score}`.\n")

    lines.append("\n## Reading the columns\n")
    lines.append(
        "- **RMSE (RUL≤25)** is the number that matters operationally. A model can "
        "post a good overall RMSE by being accurate on healthy engines while being "
        "useless near failure, which is the only region that drives a maintenance "
        "decision.\n"
        "- **late %** is the share of predictions that *overestimate* remaining "
        "life. These are the dangerous errors: an engine predicted healthier than "
        "it is gets dispatched and strands an aircraft. The NASA score already "
        "penalises them asymmetrically; this column makes the rate visible.\n"
        "- **NASA score** is a sum, not a mean, so it grows with the number of test "
        "units and is only comparable within a subset.\n"
    )

    lines.append("\n## A bug worth recording\n")
    lines.append(
        "The first run of this harness produced validation RMSE 11.0 and test RMSE "
        "**53.7** with R² **-0.80**. The cause was a leaking feature: `cycle_norm` "
        "was computed as `time_in_cycles / max(time_in_cycles)` per unit. In "
        "training, `max()` is the failure cycle, so the feature encodes *fraction "
        "of life consumed* and the model learned `cycle_norm ~ 1 => RUL ~ 0`. In "
        "test, `max()` is merely where recording stopped, so every test unit "
        "arrives with `cycle_norm ~ 1` while actually having 100+ cycles left, and "
        "the model predicted them all as nearly dead.\n\n"
        "It is recorded here because the validation score looked *excellent* while "
        "the model was worthless -- which is precisely how this class of leak "
        "survives into a published result. The fix divides by a fixed constant "
        "(400 cycles), so the feature means the same thing in both splits and is "
        "computable online from a single counter.\n"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def save_results(runs: list[ComparisonRun], output: Path) -> Path:
    """Persist raw metrics as JSON for the registry and the analytics page."""
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "subsets": {
            run.subset.value: {
                "models": {
                    name: {
                        **result.summary(),
                        "test_metrics": result.test_metrics.to_dict(),
                        "val_metrics": result.val_metrics.to_dict(),
                        "history": result.history,
                    }
                    for name, result in run.results.items()
                },
                "ensemble": run.ensemble,
                "best_rmse": run.best("rmse"),
                "best_score": run.best("nasa_score"),
            }
            for run in runs
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output
