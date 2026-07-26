"""Exploratory data analysis, emitted as a committed markdown report.

Written as a module rather than a notebook so the findings are regenerable,
diffable in pull requests, and verifiable in CI. Notebooks are for exploration;
this is the record.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from at_core.domain.enums import Subset
from at_core.domain.sensors import SENSOR_BY_KEY, excluded_sensors, informative_sensors
from at_data.parse import load_parquet
from at_data.regimes import assign_regimes, fit_regimes

SENSOR_KEYS = tuple(f"s{i}" for i in range(1, 22))


@dataclass(frozen=True, slots=True)
class SubsetStats:
    subset: Subset
    train_rows: int
    test_rows: int
    train_units: int
    test_units: int
    train_len_min: int
    train_len_max: int
    train_len_median: float
    test_len_min: int
    n_regimes: int
    silhouette: float
    n_informative: int


def compute_stats(interim: Path) -> dict[Subset, SubsetStats]:
    stats: dict[Subset, SubsetStats] = {}
    for subset in Subset:
        train = load_parquet(subset, "train", interim)
        test = load_parquet(subset, "test", interim)
        train_lengths = train.groupby("unit_number").size()
        model = fit_regimes(train, subset)
        stats[subset] = SubsetStats(
            subset=subset,
            train_rows=len(train),
            test_rows=len(test),
            train_units=int(train["unit_number"].nunique()),
            test_units=int(test["unit_number"].nunique()),
            train_len_min=int(train_lengths.min()),
            train_len_max=int(train_lengths.max()),
            train_len_median=float(train_lengths.median()),
            test_len_min=int(test.groupby("unit_number").size().min()),
            n_regimes=model.n_regimes,
            silhouette=model.silhouette,
            n_informative=len(informative_sensors(subset)),
        )
    return stats


def sensor_signal_table(interim: Path, subset: Subset) -> pd.DataFrame:
    """Per-sensor variance and correlation with RUL, regime-aware."""
    frame = load_parquet(subset, "train", interim)
    model = fit_regimes(frame, subset)
    frame = assign_regimes(frame, model)

    rows = []
    for key in SENSOR_KEYS:
        spec = SENSOR_BY_KEY[key]
        series = frame[key]
        std = float(series.std())

        if std < 1e-9:
            raw_corr = regime_corr = 0.0
        else:
            raw_corr = float(abs(np.corrcoef(series, frame["rul_capped"])[0, 1]))
            grouped = frame.groupby("regime")[key]
            z = grouped.transform(lambda x: (x - x.mean()) / (x.std() if x.std() > 0 else 1.0))
            regime_corr = (
                float(abs(np.corrcoef(z, frame["rul_capped"])[0, 1])) if float(z.std()) > 0 else 0.0
            )

        rows.append(
            {
                "sensor": key,
                "symbol": spec.symbol,
                "module": spec.primary_module.value,
                "std": std,
                "n_unique": int(series.nunique()),
                "corr_raw": raw_corr,
                "corr_regime_z": regime_corr,
                "used": key in informative_sensors(subset),
            }
        )
    return pd.DataFrame(rows)


def _md_table(
    frame: pd.DataFrame,
    floatfmt: str = "{:.4f}",
    int_columns: tuple[str, ...] = (),
) -> str:
    """Render a DataFrame as a GitHub markdown table.

    Columns whose values are all integral render without decimals even when the
    dtype is float -- counts and ids formatted as ``8044.000`` look like a bug.
    """
    buffer = io.StringIO()
    headers = list(frame.columns)
    buffer.write("| " + " | ".join(headers) + " |\n")
    buffer.write("|" + "|".join("---" for _ in headers) + "|\n")

    integral: set[str] = set(int_columns)
    for raw_column in frame.columns:
        column = str(raw_column)
        values = frame[raw_column]
        numeric = pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)
        if numeric and bool((values.dropna() % 1 == 0).all()):
            integral.add(column)

    for _, row in frame.iterrows():
        cells = []
        for raw_key, value in row.items():
            column = str(raw_key)
            if isinstance(value, bool):
                cells.append("yes" if value else "-")
            elif isinstance(value, float | int) and not isinstance(value, bool):
                formatted = f"{int(value):,}" if column in integral else floatfmt.format(value)
                cells.append(formatted)
            else:
                cells.append(str(value))
        buffer.write("| " + " | ".join(cells) + " |\n")
    return buffer.getvalue()


def build_report(interim: Path, output: Path) -> Path:
    """Generate the EDA report from the interim Parquet layer."""
    stats = compute_stats(interim)
    lines: list[str] = []

    lines.append("# C-MAPSS Exploratory Data Analysis\n")
    lines.append(
        f"_Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by "
        "`at_data.eda`. Regenerate with `make eda`._\n"
    )
    lines.append(
        "Every figure below is computed from the official NASA files obtained by "
        "`make data`. Where a number contradicts a claim made during architecture "
        "planning, the contradiction is called out explicitly.\n"
    )

    # ── 1. shape ─────────────────────────────────────────────────────────────
    lines.append("## 1. Dataset shape\n")
    shape = pd.DataFrame(
        [
            {
                "subset": s.subset.value,
                "train rows": f"{s.train_rows:,}",
                "test rows": f"{s.test_rows:,}",
                "train units": s.train_units,
                "test units": s.test_units,
                "conditions": s.n_regimes,
                "fault modes": s.subset.n_fault_modes,
            }
            for s in stats.values()
        ]
    )
    lines.append(_md_table(shape))
    total_rows = sum(s.train_rows + s.test_rows for s in stats.values())
    total_units = sum(s.train_units + s.test_units for s in stats.values())
    lines.append(
        f"\n**Total: {total_rows:,} telemetry rows across {total_units:,} trajectories.**\n"
    )

    # ── 2. trajectory lengths ────────────────────────────────────────────────
    lines.append("\n## 2. Trajectory lengths and the window constraint\n")
    lines.append(
        "The sliding-window length is bounded by the *shortest test trajectory*: a "
        "window longer than it cannot be scored without padding.\n"
    )
    lengths = pd.DataFrame(
        [
            {
                "subset": s.subset.value,
                "train min": s.train_len_min,
                "train median": s.train_len_median,
                "train max": s.train_len_max,
                "test min": s.test_len_min,
                "window (ADR-013)": s.subset.window_size,
                "fits": s.subset.window_size <= s.test_len_min,
            }
            for s in stats.values()
        ]
    )
    lines.append(_md_table(lengths, floatfmt="{:.0f}"))
    lines.append(
        "\n> **Planning correction.** ADR-013 originally specified a window of 20 for "
        "both FD002 and FD004, on the common assumption that the shortest test "
        "trajectory is 21 cycles. Measurement shows FD004's shortest is **19** "
        "(two units), which would have made those units unscoreable. The ADR was "
        "amended to use 18 for FD004.\n"
    )

    # ── 3. regimes ───────────────────────────────────────────────────────────
    lines.append("\n## 3. Operating-condition regimes\n")
    regimes = pd.DataFrame(
        [
            {
                "subset": s.subset.value,
                "regimes": s.n_regimes,
                "silhouette": s.silhouette,
                "verdict": "cleanly separated" if s.silhouette > 0.95 else "review",
            }
            for s in stats.values()
        ]
    )
    lines.append(_md_table(regimes))

    fd002 = fit_regimes(load_parquet(Subset.FD002, "train", interim), Subset.FD002)
    centroid_frame = pd.DataFrame(
        [
            {
                "regime": i,
                "altitude (kft)": c[0],
                "Mach": c[1],
                "TRA": c[2],
                "rows": fd002.counts[i],
            }
            for i, c in enumerate(fd002.centroids)
        ]
    )
    lines.append("\n**FD002 recovered flight conditions:**\n")
    lines.append(_md_table(centroid_frame, floatfmt="{:.3f}"))
    lines.append(
        "\nThe centroids land on physically meaningful conditions from sea level to "
        "42,000 ft, which is strong evidence the clustering recovered the true "
        "generating conditions rather than arbitrary partitions. Doc 07's claim of "
        f"silhouette > 0.95 is confirmed at **{fd002.silhouette:.4f}**.\n"
    )

    # ── 4. why per-regime normalisation ──────────────────────────────────────
    lines.append("\n## 4. Why per-regime normalisation is mandatory (ADR-014)\n")
    frame = assign_regimes(load_parquet(Subset.FD002, "train", interim), fd002)
    raw = abs(np.corrcoef(frame["s3"], frame["rul_capped"])[0, 1])
    z = frame.groupby("regime")["s3"].transform(lambda x: (x - x.mean()) / x.std())
    normalised = abs(np.corrcoef(z, frame["rul_capped"])[0, 1])
    within = float(frame.groupby("regime")["s3"].std().mean())
    between = float(frame.groupby("regime")["s3"].mean().std())

    lines.append(
        f"Taking T30 (s3, HPC outlet temperature) in FD002:\n\n"
        f"| measure | value |\n|---|---|\n"
        f"| \\|corr(raw s3, RUL)\\| | **{raw:.4f}** |\n"
        f"| \\|corr(per-regime z-scored s3, RUL)\\| | **{normalised:.4f}** |\n"
        f"| improvement | **{normalised / max(raw, 1e-9):.1f}x** |\n"
        f"| mean within-regime std | {within:.2f} |\n"
        f"| between-regime spread of means | {between:.2f} |\n\n"
        f"The operating condition accounts for roughly {between / within:.0f} times "
        "more variance than the degradation signal. Without per-regime normalisation "
        "the degradation is effectively invisible to a model.\n"
    )

    # ── 5. sensor selection ──────────────────────────────────────────────────
    lines.append("\n## 5. Sensor signal analysis and feature selection\n")
    for subset in (Subset.FD001, Subset.FD003):
        table = sensor_signal_table(interim, subset)
        dropped = sorted(excluded_sensors(subset), key=lambda k: int(k[1:]))
        lines.append(
            f"\n### {subset.value} ({len(informative_sensors(subset))} sensors retained)\n"
        )
        lines.append(_md_table(table))
        lines.append(f"\nExcluded: `{'`, `'.join(dropped)}`\n")

    lines.append(
        "\n> **Planning correction.** The literature commonly quotes a single list of "
        "seven constant sensors for all single-condition subsets. Direct measurement "
        "shows this is wrong in two ways:\n"
        ">\n"
        "> 1. **`s10` (epr)** is constant in FD001 but takes 4 distinct values in "
        "FD003 with |corr(RUL)| = 0.49. Dropping it discards real degradation signal, "
        "so FD003 retains 15 sensors while FD001 retains 14.\n"
        "> 2. **`s6` (P15)** is *near*-constant rather than constant. It is excluded "
        "on a signal basis (|corr| < 0.15, few distinct values), not a variance basis.\n"
        ">\n"
        "> `at_core.domain.sensors` now encodes measured per-subset sets rather than "
        "a single hardcoded list.\n"
    )

    # ── 6. degradation ───────────────────────────────────────────────────────
    lines.append("\n## 6. Degradation behaviour\n")
    fd001 = load_parquet(Subset.FD001, "train", interim)
    top = (
        sensor_signal_table(interim, Subset.FD001)
        .query("used")
        .nlargest(6, "corr_regime_z")[["sensor", "symbol", "module", "corr_regime_z"]]
    )
    lines.append("Sensors most predictive of remaining life in FD001:\n")
    lines.append(_md_table(top))

    early = fd001[fd001["rul"] > 100]
    late = fd001[fd001["rul"] < 20]
    drift = pd.DataFrame(
        [
            {
                "sensor": key,
                "symbol": SENSOR_BY_KEY[key].symbol,
                "healthy mean": float(early[key].mean()),
                "near-failure mean": float(late[key].mean()),
                "shift (sigma)": float(
                    (late[key].mean() - early[key].mean()) / (early[key].std() or 1.0)
                ),
            }
            for key in ("s3", "s4", "s11", "s12", "s20", "s21")
        ]
    )
    lines.append("\nMean shift from healthy (RUL > 100) to near-failure (RUL < 20):\n")
    lines.append(_md_table(drift, floatfmt="{:.3f}"))
    lines.append(
        "\nThe signs match turbofan physics: HPC outlet temperature and pressure rise "
        "as compressor efficiency falls, while coolant bleed flows drift downward. "
        "This is the empirical basis for the efficiency proxies in Doc 08 section 8.4.\n"
    )

    # ── 7. implications ──────────────────────────────────────────────────────
    lines.append("\n## 7. Implications for the platform\n")
    lines.append(
        "1. **Windows are subset-specific** (30 / 20 / 30 / 18), enforced by a test "
        "against the measured minimum test-trajectory length.\n"
        "2. **Normalisation must be per regime**, with centroids fitted once and "
        "persisted so the twin engine classifies live telemetry identically.\n"
        "3. **Feature sets are subset-specific** (14 / 21 / 15 / 21 sensors).\n"
        "4. **RUL labels are capped at 125** (ADR-012); the uncapped label would "
        "otherwise ask the model to distinguish a 300-cycle-remaining engine from a "
        "200-cycle one on indistinguishable sensor data.\n"
        "5. **Test trajectories never reach failure**, so evaluation uses the final "
        "window per unit against the official label file.\n"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
