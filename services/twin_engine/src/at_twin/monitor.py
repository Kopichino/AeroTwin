"""Terminal fleet monitor.

The M3 deliverable: proves the digital twin engine works end to end before a
single pixel of UI exists. Renders a live fleet view to the terminal using ANSI
escapes only -- no dependencies, so it runs anywhere the engine runs.

    python -m at_twin.monitor --subset FD002 --speed 8 --duration 30
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from at_core.domain.enums import HealthBand, Subset, TwinStatus
from at_twin.registry import TwinRegistry, fleet_summary
from at_twin.replay import CmapssFileSource, ReplayClock, SyntheticSource

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CLEAR = "\x1b[2J\x1b[H"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"

BAND_COLOUR = {
    HealthBand.HEALTHY: "\x1b[38;5;42m",
    HealthBand.WATCH: "\x1b[38;5;114m",
    HealthBand.WARNING: "\x1b[38;5;214m",
    HealthBand.CRITICAL: "\x1b[38;5;203m",
}
ACCENT = "\x1b[38;5;75m"


def bar(value: float, width: int = 18, *, maximum: float = 100.0) -> str:
    """Render a proportional bar using block characters."""
    filled = round(width * max(0.0, min(1.0, value / maximum)))
    return "█" * filled + "░" * (width - filled)


def render(registry: TwinRegistry, elapsed: float, top_n: int) -> str:
    summary = fleet_summary(registry)
    width = min(shutil.get_terminal_size((100, 40)).columns, 108)

    lines: list[str] = []
    lines.append(f"{ACCENT}{BOLD}  AeroTwin — Fleet Monitor{RESET}  {DIM}(M3 twin engine){RESET}")
    lines.append(f"  {DIM}{'─' * (width - 4)}{RESET}")

    speed = summary.speed
    cycle = int(registry.clock.cycles_at(elapsed * 1000.0))
    lines.append(
        f"  {DIM}subset{RESET} {registry.subset.value}   "
        f"{DIM}engines{RESET} {summary.engines}   "
        f"{DIM}active{RESET} {summary.active}   "
        f"{DIM}speed{RESET} {speed:g}x   "
        f"{DIM}clock{RESET} c{cycle}   "
        f"{DIM}elapsed{RESET} {elapsed:5.1f}s"
    )

    avg = summary.avg_health
    if avg >= 80:
        band = HealthBand.HEALTHY
    elif avg >= 60:
        band = HealthBand.WATCH
    else:
        band = HealthBand.WARNING
    lines.append(f"  {DIM}fleet health{RESET} {BAND_COLOUR[band]}{bar(avg)}{RESET} {avg:5.1f}")

    chips = "  ".join(
        f"{BAND_COLOUR[HealthBand(name)]}●{RESET} {name.title():<8}{count:>4}"
        for name, count in summary.by_band.items()
    )
    lines.append(f"  {chips}")
    lines.append("")

    lines.append(
        f"  {BOLD}{'TAIL':<12}{'UNIT':>5}{'CYCLE':>7}{'HEALTH':>8}  "
        f"{'':<20}{'BAND':<10}{'WORST':<11}{'RUL':>6}{RESET}"
    )

    ranked = sorted(
        (state for state in registry.states() if state.status is not TwinStatus.IDLE),
        key=lambda state: state.health_index,
    )[:top_n]

    for state in ranked:
        colour = BAND_COLOUR[state.health_band]
        runtime = registry._twins[state.engine_id]
        worst = (
            min(runtime.components.items(), key=lambda item: item[1].score)[0].value
            if runtime.components
            else "-"
        )
        rul = f"{state.prediction.rul_p50:.0f}" if state.prediction else "-"
        lines.append(
            f"  {state.spec.tail_number or '-':<12}"
            f"{state.spec.unit_number:>5}"
            f"{state.cycle:>7}"
            f"{state.health_index:>8.1f}  "
            f"{colour}{bar(state.health_index, 18)}{RESET}  "
            f"{colour}{state.health_band.value:<10}{RESET}"
            f"{worst:<11}{rul:>6}"
        )

    lines.append("")
    lines.append(f"  {DIM}sorted by health (worst first) · Ctrl-C to exit{RESET}")
    return "\n".join(lines)


def run(
    subset: Subset,
    *,
    speed: float,
    duration: float,
    interim: Path,
    top_n: int,
    use_synthetic: bool,
    refresh_hz: float = 4.0,
) -> int:
    regime_model = None
    if use_synthetic:
        source: CmapssFileSource | SyntheticSource = SyntheticSource(n_units=24, length=200)
    else:
        try:
            source = CmapssFileSource(subset, interim, "train")
        except FileNotFoundError:
            print("Dataset not found. Run `make data`, or pass --synthetic.", file=sys.stderr)
            return 1

        # Multi-regime subsets need the fitted centroids, otherwise per-regime
        # baselines collapse to a pooled mean and health becomes meaningless.
        regimes_path = interim.parent / "processed" / "regimes.json"
        if subset.n_conditions > 1 and regimes_path.is_file():
            from at_data.regimes import load_models

            regime_model = load_models(regimes_path).get(subset)

    registry = TwinRegistry(
        source,
        subset,
        clock=ReplayClock(speed=speed),
        phase_seed=42,
        regime_model=regime_model,
    )
    registry.start_all(0.0)

    frame_interval = 1.0 / refresh_hz
    started = time.perf_counter()
    tick_latencies: list[float] = []

    sys.stdout.write(HIDE_CURSOR)
    try:
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= duration:
                break

            tick_started = time.perf_counter()
            registry.tick(elapsed * 1000.0)
            tick_latencies.append((time.perf_counter() - tick_started) * 1000.0)

            sys.stdout.write(CLEAR + render(registry, elapsed, top_n) + "\n")
            sys.stdout.flush()
            time.sleep(frame_interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

    if tick_latencies:
        tick_latencies.sort()
        p99 = tick_latencies[int(len(tick_latencies) * 0.99)]
        print(
            f"\n  {len(tick_latencies)} ticks · "
            f"p50 {tick_latencies[len(tick_latencies) // 2]:.2f} ms · "
            f"p99 {p99:.2f} ms · max {tick_latencies[-1]:.2f} ms"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", default="FD002", choices=[s.value for s in Subset])
    parser.add_argument("--speed", type=float, default=8.0)
    parser.add_argument("--duration", type=float, default=30.0, help="seconds")
    parser.add_argument("--top", type=int, default=14, help="rows to display")
    parser.add_argument("--interim", type=Path, default=Path("data/interim"))
    parser.add_argument("--synthetic", action="store_true", help="run without the dataset")
    args = parser.parse_args(argv)

    return run(
        Subset(args.subset),
        speed=args.speed,
        duration=args.duration,
        interim=args.interim,
        top_n=args.top,
        use_synthetic=args.synthetic,
    )


if __name__ == "__main__":
    raise SystemExit(main())
