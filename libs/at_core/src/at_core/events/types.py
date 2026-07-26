"""Domain event catalogue and envelope (Doc 08 section 8.7).

Events are append-only facts. Their ``event_type`` strings are persisted in
``twin_events.event_type`` and published on Redis pub/sub, so renaming a member is
a breaking change requiring a migration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from at_core.domain.enums import Severity


class EventType(StrEnum):
    """Canonical event names, formatted ``domain.entity.action`` in past tense."""

    PROVISIONED = "twin.provisioned"
    STARTED = "twin.started"
    PAUSED = "twin.paused"
    RESUMED = "twin.resumed"
    RESET = "twin.reset"
    STATUS_CHANGED = "twin.status.changed"
    CYCLE_ADVANCED = "twin.cycle.advanced"
    HEALTH_UPDATED = "twin.health.updated"
    HEALTH_BAND_CHANGED = "twin.health.band_changed"
    COMPONENT_DEGRADED = "twin.component.degraded"
    PREDICTION_UPDATED = "twin.prediction.updated"
    PREDICTION_STALE = "twin.prediction.stale"
    ANOMALY_DETECTED = "twin.anomaly.detected"
    ANOMALY_RESOLVED = "twin.anomaly.resolved"
    REGIME_CHANGED = "twin.regime.changed"
    MAINTENANCE_STARTED = "twin.maintenance.started"
    MAINTENANCE_PERFORMED = "twin.maintenance.performed"
    FAILED = "twin.failed"
    RETIRED = "twin.retired"
    COMMAND_REJECTED = "twin.command.rejected"
    SIMULATION_COMPLETED = "twin.simulation.completed"
    ENGINE_LAG = "twin.engine.lag"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """An immutable fact about one twin at one point in its sequence."""

    engine_id: uuid.UUID
    seq: int
    cycle: int
    event_type: EventType
    payload: dict[str, Any]
    severity: Severity = Severity.INFO
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for persistence and bus publication."""
        return {
            "engine_id": str(self.engine_id),
            "seq": self.seq,
            "cycle": self.cycle,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "payload": self.payload,
            "ts": self.ts.isoformat(),
            "trace_id": self.trace_id,
        }
