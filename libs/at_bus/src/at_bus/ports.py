"""Event bus ports (Doc 05 section 5.1).

The twin engine publishes deltas and consumes commands through these interfaces
and never imports a broker client directly. Two adapters implement them:

* ``at_bus.memory.InMemoryBus``  -- single process, zero dependencies, used for
  local development, tests, and the standalone demo.
* ``at_bus.redis_bus.RedisBus``  -- Redis Streams + pub/sub for the Docker stack
  and any multi-process deployment.

Because both satisfy the same protocol, moving from one to the other is a single
line in the composition root. Nothing in the engine or the API changes.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Envelope:
    """Uniform wrapper for everything that crosses the bus.

    Carrying ``trace_id`` here is what allows a single request to be followed
    from the HTTP edge, through the command stream, into the twin engine and
    back out as a published delta (Doc 01 section 1.8.3).
    """

    type: str
    payload: dict[str, Any]
    channel: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = field(default_factory=_now)
    trace_id: str | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.version,
            "id": self.id,
            "ts": self.ts.isoformat(),
            "ch": self.channel,
            "type": self.type,
            "payload": self.payload,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        raw_ts = data.get("ts")
        return cls(
            type=str(data.get("type", "")),
            payload=dict(data.get("payload", {})),
            channel=str(data.get("ch", "")),
            id=str(data.get("id", uuid.uuid4().hex)),
            ts=datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else _now(),
            trace_id=data.get("trace_id"),
            version=int(data.get("v", 1)),
        )


Subscriber = Callable[[Envelope], None]


class EventBus(ABC):
    """Publish/subscribe transport for twin deltas and fleet events."""

    @abstractmethod
    async def publish(self, channel: str, envelope: Envelope) -> None:
        """Fan an envelope out to every subscriber of ``channel``."""

    @abstractmethod
    def subscribe(self, channel: str) -> AsyncIterator[Envelope]:
        """Async iterator over envelopes for one channel.

        Implementations must apply backpressure by dropping the oldest
        state-replacing frames rather than growing an unbounded queue (P7).
        """

    @abstractmethod
    async def close(self) -> None:
        """Release resources and wake any pending subscribers."""


class CommandBus(Protocol):
    """Durable, at-least-once delivery of commands to the twin engine."""

    async def send(self, envelope: Envelope) -> None: ...

    def consume(self, group: str, consumer: str) -> AsyncIterator[Envelope]: ...


# ── channel naming (Doc 13 section 13.3) ────────────────────────────────────

CHANNEL_FLEET = "fleet"
CHANNEL_SYSTEM = "system"
CHANNEL_NOTIFICATIONS = "notifications"


def twin_channel(engine_id: uuid.UUID | str) -> str:
    return f"twin:{engine_id}"


def agent_channel(run_id: uuid.UUID | str) -> str:
    return f"agent:{run_id}"


def sim_channel(simulation_id: uuid.UUID | str) -> str:
    return f"sim:{simulation_id}"
