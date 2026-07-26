"""Canonical enumerations for the AeroTwin domain.

These values are the single source of truth referenced by the database schema
(Doc 04), the wire contracts (Doc 12/13) and the frontend design tokens (Doc 06).
Changing a member here is a breaking change across the whole platform.
"""

from __future__ import annotations

from enum import StrEnum


class Subset(StrEnum):
    """NASA C-MAPSS data subsets. See Doc 00 section 0.6."""

    FD001 = "FD001"
    FD002 = "FD002"
    FD003 = "FD003"
    FD004 = "FD004"

    @property
    def n_conditions(self) -> int:
        """Number of distinct operating-condition regimes in this subset."""
        return 6 if self in (Subset.FD002, Subset.FD004) else 1

    @property
    def n_fault_modes(self) -> int:
        """Number of fault modes present (HPC only, or HPC + Fan)."""
        return 2 if self in (Subset.FD003, Subset.FD004) else 1

    @property
    def window_size(self) -> int:
        """Sliding-window length for RUL models (ADR-013, amended in M2).

        The binding constraint is the shortest *test* trajectory: a window longer
        than it cannot be scored without padding. Measured against the real NASA
        files rather than assumed from the literature:

            FD001 min 31, FD003 min 38  -> 30
            FD002 min 21                -> 20
            FD004 min 19                -> 18  (2 units have only 19 cycles)
        """
        if self is Subset.FD004:
            return 18
        if self is Subset.FD002:
            return 20
        return 30

    @property
    def min_test_trajectory(self) -> int:
        """Shortest test trajectory, verified empirically in M2 ingestion."""
        return {
            Subset.FD001: 31,
            Subset.FD002: 21,
            Subset.FD003: 38,
            Subset.FD004: 19,
        }[self]


class TwinStatus(StrEnum):
    """Digital-twin lifecycle states. Transitions are defined in domain.fsm."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    MAINTENANCE = "MAINTENANCE"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class HealthBand(StrEnum):
    """Health classification bands. Thresholds live in domain.health."""

    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Ordinal severity, 0 = healthiest. Enables comparison and sorting."""
        return _BAND_RANK[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, HealthBand):
            return NotImplemented
        return self.rank < other.rank


_BAND_RANK: dict[HealthBand, int] = {
    HealthBand.HEALTHY: 0,
    HealthBand.WATCH: 1,
    HealthBand.WARNING: 2,
    HealthBand.CRITICAL: 3,
}


class Severity(StrEnum):
    """Event / anomaly / work-package severity."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EngineModule(StrEnum):
    """Physical turbofan sections used for component-level health (Doc 08)."""

    FAN = "FAN"
    LPC = "LPC"
    HPC = "HPC"
    COMBUSTOR = "COMBUSTOR"
    HPT = "HPT"
    LPT = "LPT"
    NOZZLE = "NOZZLE"
    BEARINGS = "BEARINGS"
    CONTROL = "CONTROL"


class CommandType(StrEnum):
    """Commands accepted by the twin engine (single-writer rule, Doc 01 section 1.5)."""

    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    SEEK = "SEEK"
    SET_SPEED = "SET_SPEED"
    RESET = "RESET"
    INJECT_FAULT = "INJECT_FAULT"
    SIMULATE = "SIMULATE"
    PERFORM_MAINTENANCE = "PERFORM_MAINTENANCE"
    RETIRE = "RETIRE"


class FaultMode(StrEnum):
    """Diagnosis vocabulary used by the Failure Diagnosis agent (Doc 09 section 9.5)."""

    HPC_EFFICIENCY_LOSS = "HPC_EFFICIENCY_LOSS"
    HPC_FLOW_CAPACITY_LOSS = "HPC_FLOW_CAPACITY_LOSS"
    FAN_DEGRADATION = "FAN_DEGRADATION"
    HPT_BLADE_TIP_CLEARANCE = "HPT_BLADE_TIP_CLEARANCE"
    COOLING_BLEED_DRIFT = "COOLING_BLEED_DRIFT"
    COMBUSTOR_INEFFICIENCY = "COMBUSTOR_INEFFICIENCY"
    SENSOR_FAULT = "SENSOR_FAULT"
    UNKNOWN = "UNKNOWN"


class ReplaySpeed(StrEnum):
    """Discrete replay multipliers exposed in the UI (Doc 08 section 8.8)."""

    X0_5 = "0.5"
    X1 = "1"
    X2 = "2"
    X4 = "4"
    X8 = "8"
    X16 = "16"
    X32 = "32"

    @property
    def multiplier(self) -> float:
        return float(self.value)
