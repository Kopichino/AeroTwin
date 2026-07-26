"""Physics-informed component health kernel (Doc 08 section 8.4).

C-MAPSS telemetry is produced by a real thermodynamic engine simulation, so its
sensors obey turbofan physics. This module exploits that: instead of inventing a
weighted sensor sum, it derives module health from **efficiency and flow-capacity
proxies**, each a monotone function of a specific module's deterioration.

Every proxy is expressed as a deviation from the engine's *own* healthy baseline,
so unit-to-unit manufacturing scatter cancels out. That is what makes the answer to
"how do you know the HPC is at 62 percent?" defensible to an aero examiner.

Empirical grounding (measured in M2, FD001 unit 1, healthy -> failure):

    T30/T24 ratio   2.4703 -> 2.4861   (+0.64 %)   HPC efficiency loss
    T50             1399.6 -> 1425.9   (+1.79 %)   turbine gas-path deterioration
    Ps30              47.27 ->   48.16 (+1.89 %)   HPC discharge pressure rise
    W31               39.00 ->   38.48 (-1.33 %)   coolant bleed drift

This module is pure: no I/O, no randomness, no awaits. It is exhaustively unit
testable and deterministic, which is what lets the whole twin be replayed exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from at_core.domain.enums import EngineModule
from at_core.domain.health import ComponentState, clamp

#: Number of leading cycles used to establish an engine's healthy baseline.
BASELINE_CYCLES: Final[int] = 20

#: Minimum cycles before component health is reported. Below this the baseline is
#: not yet trustworthy and every module is reported as nominal.
MIN_CYCLES_FOR_HEALTH: Final[int] = 5

#: EWMA factor applied to component scores. Sensor noise on a single cycle is
#: comparable to a whole cycle of real degradation, so unsmoothed scores jitter by
#: several points and make the fleet dashboard flicker. 0.15 keeps roughly a
#: 12-cycle memory, which tracks genuine trend while rejecting single-cycle noise.
COMPONENT_SCORE_ALPHA: Final[float] = 0.15


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """One thermodynamic proxy: how to compute it and what it means.

    Attributes:
        name: Stable identifier, surfaced in ``ComponentState.drivers``.
        module: Engine module whose deterioration this proxy measures.
        direction: ``+1`` if the proxy *increases* as the module degrades,
            ``-1`` if it decreases. Used to sign the deviation.
        scale: Fractional deviation from baseline that corresponds to
            end-of-life deterioration for this proxy. Calibrated so a component
            score of 50 sits near the population median at half life.
        weight: Relative contribution when several proxies target one module.
    """

    name: str
    module: EngineModule
    direction: int
    scale: float
    weight: float = 1.0


#: The proxy catalogue. Scales are derived from the M2 measurements above, taking
#: roughly 1.5x the observed healthy-to-failure excursion so that a fully failed
#: engine lands near zero rather than saturating early.
PROXIES: Final[tuple[ProxySpec, ...]] = (
    # HPC: compressor temperature rise above ideal, and discharge pressure rise.
    ProxySpec("hpc_temp_ratio", EngineModule.HPC, +1, 0.010, 1.0),
    ProxySpec("hpc_discharge_pressure", EngineModule.HPC, +1, 0.028, 0.8),
    ProxySpec("hpc_bleed_enthalpy", EngineModule.HPC, +1, 0.015, 0.6),
    # Exhaust gas temperature also rises when HPC efficiency falls: the core must
    # burn more fuel for the same thrust, so EGT is partly an HPC symptom rather
    # than a purely turbine-side one. Attributing all of T50 to the HPT made the
    # HPT the worst module on 35 % of FD001 units, whose documented fault mode is
    # HPC degradation. Sharing it matches both the physics and the ground truth.
    ProxySpec("hpt_outlet_temp", EngineModule.HPC, +1, 0.027, 0.7),
    # LPC: booster temperature rise.
    ProxySpec("lpc_temp_rise", EngineModule.LPC, +1, 0.006, 1.0),
    # HPT: coolant bleed drift is turbine-specific (seal and tip-clearance wear);
    # EGT contributes but is not allowed to dominate, for the reason above.
    ProxySpec("hpt_outlet_temp", EngineModule.HPT, +1, 0.027, 0.6),
    ProxySpec("hpt_coolant_bleed", EngineModule.HPT, -1, 0.020, 1.0),
    # LPT: coolant bleed drift on the low-pressure turbine.
    ProxySpec("lpt_coolant_bleed", EngineModule.LPT, -1, 0.022, 1.0),
    # Combustor: fuel required to hold the same output.
    ProxySpec("combustor_fuel_ratio", EngineModule.COMBUSTOR, -1, 0.008, 1.0),
    # Fan: bypass ratio drift and corrected-speed divergence.
    ProxySpec("fan_bypass_ratio", EngineModule.FAN, +1, 0.019, 1.0),
    ProxySpec("fan_speed_divergence", EngineModule.FAN, +1, 0.004, 0.5),
    # Nozzle / overall: core speed droop as the gas path deteriorates.
    ProxySpec("core_speed_droop", EngineModule.NOZZLE, -1, 0.0035, 1.0),
)

PROXIES_BY_MODULE: Final[MappingProxyType[EngineModule, tuple[ProxySpec, ...]]] = MappingProxyType(
    {
        module: tuple(p for p in PROXIES if p.module is module)
        for module in {p.module for p in PROXIES}
    }
)


def compute_proxies(sensors: dict[str, float]) -> dict[str, float]:
    """Compute raw thermodynamic proxy values from one telemetry row.

    Returns only the proxies whose inputs are present and non-degenerate, so a
    subset with missing channels degrades gracefully rather than raising.
    """

    def get(key: str) -> float | None:
        value = sensors.get(key)
        if value is None or not math.isfinite(value):
            return None
        return value

    values: dict[str, float] = {}

    t24, t30, t50 = get("s2"), get("s3"), get("s4")
    p30, ps30 = get("s7"), get("s11")
    nc = get("s9")
    nf, nrf = get("s8"), get("s13")
    phi, bpr = get("s12"), get("s15")
    htbleed = get("s17")
    w31, w32 = get("s20"), get("s21")

    # HPC: T30/T24 is the compressor temperature rise. A less efficient compressor
    # imparts more heat for the same pressure ratio, so the ratio climbs.
    if t24 and t30 and t24 > 0:
        values["hpc_temp_ratio"] = t30 / t24
    if ps30 is not None:
        values["hpc_discharge_pressure"] = ps30
    if htbleed is not None:
        values["hpc_bleed_enthalpy"] = htbleed

    # LPC: booster temperature rise relative to the (constant) fan inlet.
    if t24 is not None:
        values["lpc_temp_rise"] = t24

    # HPT: exhaust gas temperature is the classic turbine deterioration indicator.
    if t50 is not None:
        values["hpt_outlet_temp"] = t50
    if w31 is not None:
        values["hpt_coolant_bleed"] = w31
    if w32 is not None:
        values["lpt_coolant_bleed"] = w32

    # Combustor: fuel flow per unit static pressure.
    if phi is not None:
        values["combustor_fuel_ratio"] = phi

    # Fan: bypass ratio drifts as fan aero deteriorates; physical and corrected
    # fan speeds diverge as the fan works harder for the same commanded thrust.
    if bpr is not None:
        values["fan_bypass_ratio"] = bpr
    if nf and nrf and nrf > 0:
        values["fan_speed_divergence"] = nf / nrf

    # Overall gas path: physical core speed droops as the gas path deteriorates.
    # Measured on FD001 unit 1: Nc falls 0.11 % from baseline to failure. The
    # Nc/NRc *ratio* was tried first and rejected -- correcting for inlet
    # conditions cancels the droop and inverts the sign (+0.11 %), which would
    # have reported an improving nozzle on a failing engine.
    if nc is not None:
        values["core_speed_droop"] = nc
    if p30 is not None:
        values.setdefault("hpc_discharge_pressure", p30)

    return values


@dataclass(frozen=True, slots=True)
class BaselineAccumulator:
    """Per-regime running mean of proxy values over healthy opening cycles.

    **Baselines must be kept per operating regime.** In FD002/FD004 the six flight
    conditions move T30 by 350 degR and fuel ratio by a factor of four, while a
    whole life of degradation moves T30 by roughly 14 degR. A single pooled mean
    therefore measures which regime the engine is flying, not how worn it is --
    during M3 this made the combustor look like the worst module on essentially
    every FD002 engine, because ``phi`` has the widest regime spread. This is the
    same effect quantified in ADR-014 and docs/reports/eda.md section 4.

    Immutable: ``observe`` returns a new accumulator, preserving the purity of the
    twin transition functions.
    """

    per_regime: MappingProxyType[int, MappingProxyType[str, float]] = MappingProxyType({})
    counts: MappingProxyType[int, int] = MappingProxyType({})

    @property
    def total_count(self) -> int:
        return sum(self.counts.values())

    def is_ready_for(self, regime: int) -> bool:
        """Whether this regime has enough observations to judge deviation."""
        return self.counts.get(regime, 0) >= MIN_CYCLES_FOR_HEALTH

    @property
    def is_ready(self) -> bool:
        return any(count >= MIN_CYCLES_FOR_HEALTH for count in self.counts.values())

    @property
    def is_complete(self) -> bool:
        """Complete once every observed regime has a full baseline."""
        if not self.counts:
            return False
        return all(count >= BASELINE_CYCLES for count in self.counts.values())

    def observe(self, proxies: dict[str, float], regime: int = 0) -> BaselineAccumulator:
        """Fold one cycle of proxy values into this regime's baseline."""
        if self.counts.get(regime, 0) >= BASELINE_CYCLES:
            return self

        merged_regimes = {key: dict(value) for key, value in self.per_regime.items()}
        bucket = merged_regimes.setdefault(regime, {})
        for key, value in proxies.items():
            bucket[key] = bucket.get(key, 0.0) + value

        merged_counts = dict(self.counts)
        merged_counts[regime] = merged_counts.get(regime, 0) + 1

        return BaselineAccumulator(
            per_regime=MappingProxyType(
                {key: MappingProxyType(value) for key, value in merged_regimes.items()}
            ),
            counts=MappingProxyType(merged_counts),
        )

    def mean(self, regime: int = 0) -> dict[str, float]:
        """Baseline value per proxy for one regime."""
        count = self.counts.get(regime, 0)
        if count == 0:
            return {}
        return {key: total / count for key, total in self.per_regime[regime].items()}


def _deviation(current: float, baseline: float, spec: ProxySpec) -> float:
    """Signed, normalised deterioration in [0, 1].

    Zero means at-or-better-than baseline; one means fully deteriorated. The sign
    convention in ``spec.direction`` makes "worse" always positive.
    """
    if baseline == 0.0 or not math.isfinite(baseline):
        return 0.0
    fractional = (current - baseline) / abs(baseline)
    deterioration = fractional * spec.direction
    return clamp(deterioration / spec.scale)


def _logistic_score(deterioration: float) -> float:
    """Map deterioration in [0, 1] onto a 0-100 health score.

    A logistic curve centred at 0.5 is used rather than a linear map so that early
    wear registers gently while late-life deterioration moves the score sharply --
    which matches how maintenance engineers actually reason about condition.
    """
    steepness = 6.0
    raw = 1.0 / (1.0 + math.exp(steepness * (deterioration - 0.5)))
    # Rescale so deterioration 0 -> 100 and 1 -> 0 exactly.
    low = 1.0 / (1.0 + math.exp(steepness * 0.5))
    high = 1.0 / (1.0 + math.exp(-steepness * 0.5))
    return clamp((raw - low) / (high - low), 0.0, 1.0) * 100.0


def compute_component_health(
    sensors: dict[str, float],
    baseline: BaselineAccumulator,
    *,
    previous: dict[EngineModule, ComponentState] | None = None,
    cycle: int = 0,
    regime: int = 0,
) -> dict[EngineModule, ComponentState]:
    """Derive per-module health from one telemetry row and the engine's baseline.

    Args:
        sensors: Current sensor values keyed ``s1``..``s21``.
        baseline: Accumulated healthy-baseline proxy means for this engine.
        previous: Prior component states, used to compute degradation rates.
        cycle: Current engine cycle, recorded on the returned states.

    Returns:
        A health score per tracked module. Before the baseline is established,
        every module is reported nominal rather than guessed at.
    """
    from at_core.domain.sensors import TRACKED_MODULES

    if not baseline.is_ready_for(regime):
        # No trustworthy reference for this flight condition yet. Hold the
        # previous assessment rather than inventing one or resetting to nominal.
        if previous:
            return dict(previous)
        return {
            module: ComponentState(module=module, score=100.0, drivers=())
            for module in TRACKED_MODULES
        }

    current = compute_proxies(sensors)
    reference = baseline.mean(regime)

    result: dict[EngineModule, ComponentState] = {}
    for module in TRACKED_MODULES:
        specs = PROXIES_BY_MODULE.get(module, ())
        contributions: list[tuple[str, float, float]] = []

        for spec in specs:
            if spec.name not in current or spec.name not in reference:
                continue
            deterioration = _deviation(current[spec.name], reference[spec.name], spec)
            contributions.append((spec.name, deterioration, spec.weight))

        if not contributions:
            result[module] = ComponentState(module=module, score=100.0, drivers=())
            continue

        total_weight = sum(weight for _, _, weight in contributions)
        deterioration = sum(value * weight for _, value, weight in contributions) / total_weight
        score = _logistic_score(deterioration)

        # Smooth against the previous score: a single noisy cycle must not move a
        # module across a health band (see COMPONENT_SCORE_ALPHA).
        if previous and module in previous:
            score = (
                COMPONENT_SCORE_ALPHA * score
                + (1.0 - COMPONENT_SCORE_ALPHA) * previous[module].score
            )

        # Drivers: the proxies pushing this module down hardest, for XAI and the
        # diagnosis agent. Only meaningful deterioration is reported.
        drivers = tuple(
            name for name, value, _ in sorted(contributions, key=lambda c: -c[1]) if value > 0.05
        )

        rate = 0.0
        if previous and module in previous:
            rate = score - previous[module].score

        result[module] = ComponentState(
            module=module,
            score=score,
            degradation_rate=rate,
            drivers=drivers,
            last_maintained_cycle=(
                previous[module].last_maintained_cycle if previous and module in previous else None
            ),
        )

    return result


def apply_maintenance(
    components: dict[EngineModule, ComponentState],
    module: EngineModule,
    effectiveness: float,
    cycle: int,
) -> dict[EngineModule, ComponentState]:
    """Restore one module toward nominal by an effectiveness factor.

    Effectiveness reflects the depth of the action (Doc 08 section 8.10):
    inspect 0.05, wash 0.25, repair 0.6, overhaul 0.98.
    """
    result = dict(components)
    existing = result.get(module)
    if existing is None:
        return result
    restored = existing.score + (100.0 - existing.score) * clamp(effectiveness)
    result[module] = ComponentState(
        module=module,
        score=clamp(restored, 0.0, 100.0),
        degradation_rate=0.0,
        drivers=(),
        last_maintained_cycle=cycle,
    )
    return result
