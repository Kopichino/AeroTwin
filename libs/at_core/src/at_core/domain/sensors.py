"""C-MAPSS sensor catalogue and sensor -> module attribution matrix.

This module encodes the physical meaning of the 21 C-MAPSS sensors (Doc 00
section 0.6) and the attribution weights used to map sensor deviations onto
engine modules (Doc 08 section 8.5). It is pure data plus lookup helpers -- no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from at_core.domain.enums import EngineModule, Subset

N_SENSORS: Final[int] = 21
N_OP_SETTINGS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class SensorSpec:
    """Static description of one C-MAPSS sensor channel."""

    index: int
    """1-based sensor number as it appears in the raw dataset."""
    symbol: str
    """Engineering symbol, e.g. ``T30``."""
    description: str
    unit: str
    primary_module: EngineModule

    @property
    def key(self) -> str:
        """Wire/database column name, e.g. ``s3``."""
        return f"s{self.index}"


SENSOR_SPECS: Final[tuple[SensorSpec, ...]] = (
    SensorSpec(1, "T2", "Total temperature at fan inlet", "degR", EngineModule.FAN),
    SensorSpec(2, "T24", "Total temperature at LPC outlet", "degR", EngineModule.LPC),
    SensorSpec(3, "T30", "Total temperature at HPC outlet", "degR", EngineModule.HPC),
    SensorSpec(4, "T50", "Total temperature at LPT outlet", "degR", EngineModule.LPT),
    SensorSpec(5, "P2", "Pressure at fan inlet", "psia", EngineModule.FAN),
    SensorSpec(6, "P15", "Total pressure in bypass duct", "psia", EngineModule.FAN),
    SensorSpec(7, "P30", "Total pressure at HPC outlet", "psia", EngineModule.HPC),
    SensorSpec(8, "Nf", "Physical fan speed", "rpm", EngineModule.FAN),
    SensorSpec(9, "Nc", "Physical core speed", "rpm", EngineModule.HPC),
    SensorSpec(10, "epr", "Engine pressure ratio (P50/P2)", "-", EngineModule.NOZZLE),
    SensorSpec(11, "Ps30", "Static pressure at HPC outlet", "psia", EngineModule.HPC),
    SensorSpec(12, "phi", "Ratio of fuel flow to Ps30", "pps/psi", EngineModule.COMBUSTOR),
    SensorSpec(13, "NRf", "Corrected fan speed", "rpm", EngineModule.FAN),
    SensorSpec(14, "NRc", "Corrected core speed", "rpm", EngineModule.HPC),
    SensorSpec(15, "BPR", "Bypass ratio", "-", EngineModule.FAN),
    SensorSpec(16, "farB", "Burner fuel-air ratio", "-", EngineModule.COMBUSTOR),
    SensorSpec(17, "htBleed", "Bleed enthalpy", "-", EngineModule.HPC),
    SensorSpec(18, "Nf_dmd", "Demanded fan speed", "rpm", EngineModule.CONTROL),
    SensorSpec(19, "PCNfR_dmd", "Demanded corrected fan speed", "rpm", EngineModule.CONTROL),
    SensorSpec(20, "W31", "HPT coolant bleed", "lbm/s", EngineModule.HPT),
    SensorSpec(21, "W32", "LPT coolant bleed", "lbm/s", EngineModule.LPT),
)

SENSOR_BY_KEY: Final[MappingProxyType[str, SensorSpec]] = MappingProxyType(
    {spec.key: spec for spec in SENSOR_SPECS}
)
SENSOR_BY_SYMBOL: Final[MappingProxyType[str, SensorSpec]] = MappingProxyType(
    {spec.symbol: spec for spec in SENSOR_SPECS}
)

#: Sensors with zero variance across the whole training split, measured directly
#: from the NASA files in M2 (not taken from the literature, which commonly quotes
#: a single seven-sensor list for all subsets -- that is wrong).
#:
#: Two findings that the common list gets wrong:
#:   * ``s10`` (epr) is constant in FD001 but takes 4 values in FD003 with
#:     |corr(RUL)| = 0.49. Dropping it discards real degradation signal.
#:   * ``s6`` (P15) is near-constant but not constant (2 values in FD001,
#:     17 in FD003); it is excluded on a signal basis, not a variance basis.
ZERO_VARIANCE_SENSORS: Final[MappingProxyType[Subset, frozenset[str]]] = MappingProxyType(
    {
        Subset.FD001: frozenset({"s1", "s5", "s10", "s16", "s18", "s19"}),
        Subset.FD002: frozenset(),
        Subset.FD003: frozenset({"s1", "s5", "s16", "s18", "s19"}),
        Subset.FD004: frozenset(),
    }
)

#: Additionally excluded for single-regime subsets: measurable variance but
#: negligible correlation with degradation (|corr(RUL)| < 0.15) and fewer than
#: 20 distinct values, i.e. quantisation noise rather than signal.
LOW_SIGNAL_SENSORS: Final[MappingProxyType[Subset, frozenset[str]]] = MappingProxyType(
    {
        Subset.FD001: frozenset({"s6"}),
        Subset.FD002: frozenset(),
        Subset.FD003: frozenset({"s6"}),
        Subset.FD004: frozenset(),
    }
)

#: Sensor -> module attribution weights (Doc 08 section 8.5). Each row sums to 1.0.
#: Used for anomaly attribution, XAI module mapping and 3D hotspot placement.
ATTRIBUTION_MATRIX: Final[MappingProxyType[str, MappingProxyType[EngineModule, float]]] = (
    MappingProxyType(
        {
            key: MappingProxyType(weights)
            for key, weights in {
                "s2": {EngineModule.FAN: 0.2, EngineModule.LPC: 0.8},
                "s3": {EngineModule.LPC: 0.1, EngineModule.HPC: 0.9},
                "s4": {
                    EngineModule.HPC: 0.1,
                    EngineModule.COMBUSTOR: 0.2,
                    EngineModule.HPT: 0.5,
                    EngineModule.LPT: 0.2,
                },
                "s7": {EngineModule.LPC: 0.1, EngineModule.HPC: 0.9},
                "s8": {EngineModule.FAN: 0.8, EngineModule.LPC: 0.2},
                "s9": {EngineModule.HPC: 0.5, EngineModule.HPT: 0.5},
                "s11": {EngineModule.LPC: 0.1, EngineModule.HPC: 0.9},
                "s12": {
                    EngineModule.HPC: 0.2,
                    EngineModule.COMBUSTOR: 0.7,
                    EngineModule.HPT: 0.1,
                },
                "s13": {EngineModule.FAN: 0.8, EngineModule.LPC: 0.2},
                "s14": {EngineModule.HPC: 0.6, EngineModule.HPT: 0.4},
                "s15": {EngineModule.FAN: 0.6, EngineModule.LPC: 0.2, EngineModule.HPC: 0.2},
                "s17": {
                    EngineModule.HPC: 0.7,
                    EngineModule.COMBUSTOR: 0.2,
                    EngineModule.HPT: 0.1,
                },
                "s20": {EngineModule.HPC: 0.2, EngineModule.HPT: 0.8},
                "s21": {EngineModule.HPC: 0.1, EngineModule.HPT: 0.2, EngineModule.LPT: 0.7},
                "s10": {
                    EngineModule.FAN: 0.2,
                    EngineModule.LPC: 0.1,
                    EngineModule.HPC: 0.2,
                    EngineModule.COMBUSTOR: 0.1,
                    EngineModule.HPT: 0.2,
                    EngineModule.LPT: 0.1,
                    EngineModule.NOZZLE: 0.1,
                },
            }.items()
        }
    )
)

#: Criticality weights for the component-weighted health mean (Doc 08 section 8.6).
MODULE_CRITICALITY: Final[MappingProxyType[EngineModule, float]] = MappingProxyType(
    {
        EngineModule.HPT: 0.22,
        EngineModule.HPC: 0.22,
        EngineModule.COMBUSTOR: 0.16,
        EngineModule.LPT: 0.14,
        EngineModule.FAN: 0.12,
        EngineModule.LPC: 0.09,
        EngineModule.NOZZLE: 0.05,
    }
)

#: Modules that receive a health score in the twin. Order is display order.
TRACKED_MODULES: Final[tuple[EngineModule, ...]] = (
    EngineModule.FAN,
    EngineModule.LPC,
    EngineModule.HPC,
    EngineModule.COMBUSTOR,
    EngineModule.HPT,
    EngineModule.LPT,
    EngineModule.NOZZLE,
)


def excluded_sensors(subset: Subset) -> frozenset[str]:
    """Sensor keys dropped from the feature set for this subset."""
    return ZERO_VARIANCE_SENSORS[subset] | LOW_SIGNAL_SENSORS[subset]


def informative_sensors(subset: Subset) -> tuple[str, ...]:
    """Return the sensor keys that carry signal for the given subset.

    Multi-regime subsets keep all 21 channels: operating-condition variation makes
    even the otherwise-static channels informative once regime is accounted for.
    Counts are FD001 14, FD002 21, FD003 15, FD004 21.
    """
    dropped = excluded_sensors(subset)
    return tuple(spec.key for spec in SENSOR_SPECS if spec.key not in dropped)


def attribute_to_modules(sensor_scores: dict[str, float]) -> dict[EngineModule, float]:
    """Distribute per-sensor magnitudes onto engine modules.

    Args:
        sensor_scores: Mapping of sensor key -> non-negative magnitude, typically
            ``abs(z_score)`` for anomaly attribution or ``abs(attribution)`` for XAI.

    Returns:
        Mapping of module -> accumulated weighted score. Modules with no
        contribution are omitted. Callers normalise if they need a distribution.
    """
    totals: dict[EngineModule, float] = {}
    for key, magnitude in sensor_scores.items():
        weights = ATTRIBUTION_MATRIX.get(key)
        if weights is None:
            spec = SENSOR_BY_KEY.get(key)
            if spec is None:
                continue
            weights = MappingProxyType({spec.primary_module: 1.0})
        for module, weight in weights.items():
            totals[module] = totals.get(module, 0.0) + magnitude * weight
    return totals


def dominant_module(sensor_scores: dict[str, float]) -> EngineModule | None:
    """Return the module with the largest attributed score, or None if empty."""
    totals = attribute_to_modules(sensor_scores)
    if not totals:
        return None
    return max(totals.items(), key=lambda item: item[1])[0]
