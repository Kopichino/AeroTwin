"""WebSocket gateway (Doc 13).

One multiplexed socket per browser tab. Channels are subscribed inside the
connection rather than opened as separate sockets, so a fleet view watching 260
engines plus one detail view still costs exactly one TCP connection.

Protocol summary:
    client -> {"type": "subscribe", "channels": [...]}
    server -> {"type": "subscribed", "channels": [...]}  then a snapshot
    server -> {"v":1, "id":..., "ch":..., "type":"twin.delta", "payload":{...}}
    client -> {"type": "ping", "seq": n}   server -> {"type": "pong", "seq": n}

Correctness never depends on lossless delivery: every subscribe is answered with
a full snapshot first, and deltas are state-replacing, so a dropped frame is
superseded rather than corrupting the client's view.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from at_bus import Envelope, EventBus

logger = structlog.get_logger(__name__)

MAX_CHANNELS_PER_CONNECTION = 64
MAX_FRAME_BYTES = 8192
HEARTBEAT_SECONDS = 15

CLOSE_BAD_FRAME = 4400
CLOSE_TOO_MANY_CHANNELS = 4403


@dataclass(slots=True)
class Connection:
    """One live browser connection and its channel subscriptions."""

    socket: WebSocket
    connection_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    channels: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    sent: int = 0

    async def send(self, message: dict[str, Any]) -> None:
        await self.socket.send_text(json.dumps(message, default=str))
        self.sent += 1


class WebSocketGateway:
    """Bridges the event bus to connected browsers.

    Snapshot providers are injected rather than imported so the gateway has no
    dependency on the twin engine: it works equally well against a live registry
    or a stub in tests.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._connections: dict[str, Connection] = {}
        self._snapshot_providers: dict[str, Any] = {}

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def register_snapshot_provider(self, prefix: str, provider: Any) -> None:
        """Register a callable returning the current state of a channel.

        ``prefix`` is matched against the channel name up to the first colon, so
        one provider serves every ``twin:<id>`` channel.
        """
        self._snapshot_providers[prefix] = provider

    def _snapshot_for(self, channel: str) -> dict[str, Any] | None:
        prefix = channel.split(":", 1)[0]
        provider = self._snapshot_providers.get(prefix)
        if provider is None:
            return None
        argument = channel.split(":", 1)[1] if ":" in channel else None
        try:
            result = provider(argument) if argument is not None else provider()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("snapshot_failed", channel=channel, error=str(exc))
            return None
        return result if isinstance(result, dict) else None

    async def handle(self, socket: WebSocket) -> None:
        """Serve one connection until it disconnects."""
        await socket.accept()
        connection = Connection(socket=socket)
        self._connections[connection.connection_id] = connection

        logger.info(
            "ws_connected",
            connection_id=connection.connection_id,
            total=len(self._connections),
        )

        await connection.send(
            {
                "type": "welcome",
                "connection_id": connection.connection_id,
                "heartbeat_s": HEARTBEAT_SECONDS,
                "protocol": 1,
            }
        )

        try:
            while True:
                raw = await socket.receive_text()
                if len(raw) > MAX_FRAME_BYTES:
                    await socket.close(code=CLOSE_BAD_FRAME, reason="frame too large")
                    return
                await self._on_client_frame(connection, raw)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - transport level
            logger.warning("ws_error", connection_id=connection.connection_id, error=str(exc))
        finally:
            await self._teardown(connection)

    async def _on_client_frame(self, connection: Connection, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            await connection.send({"type": "error", "message": "malformed JSON"})
            return

        kind = frame.get("type")

        if kind == "ping":
            await connection.send({"type": "pong", "seq": frame.get("seq")})

        elif kind == "subscribe":
            channels = frame.get("channels") or []
            if not isinstance(channels, list):
                await connection.send({"type": "error", "message": "channels must be a list"})
                return
            accepted = [
                channel
                for channel in channels[:MAX_CHANNELS_PER_CONNECTION]
                if isinstance(channel, str) and channel not in connection.channels
            ]
            for channel in accepted:
                connection.channels[channel] = asyncio.create_task(self._pump(connection, channel))
            await connection.send({"type": "subscribed", "channels": accepted})

            # Snapshot first, then deltas: a newly opened view is correct
            # immediately rather than blank until the next change.
            for channel in accepted:
                snapshot = self._snapshot_for(channel)
                if snapshot is not None:
                    await connection.send(
                        {
                            "v": 1,
                            "ch": channel,
                            "type": f"{channel.split(':', 1)[0]}.snapshot",
                            "payload": snapshot,
                        }
                    )

        elif kind == "unsubscribe":
            for channel in frame.get("channels") or []:
                task = connection.channels.pop(channel, None)
                if task is not None:
                    task.cancel()
            await connection.send({"type": "unsubscribed", "channels": frame.get("channels")})

        else:
            await connection.send({"type": "error", "message": f"unknown frame type: {kind}"})

    async def _pump(self, connection: Connection, channel: str) -> None:
        """Forward bus envelopes for one channel to one connection."""
        try:
            async for envelope in self._bus.subscribe(channel):
                await connection.send(envelope.to_dict())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - transport level
            logger.debug("ws_pump_stopped", channel=channel, error=str(exc))

    async def _teardown(self, connection: Connection) -> None:
        for task in connection.channels.values():
            task.cancel()
        for task in connection.channels.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        connection.channels.clear()
        self._connections.pop(connection.connection_id, None)
        logger.info(
            "ws_disconnected",
            connection_id=connection.connection_id,
            frames_sent=connection.sent,
            remaining=len(self._connections),
        )

    async def broadcast(self, channel: str, envelope: Envelope) -> None:
        """Publish through the bus; connections receive it via their pumps."""
        await self._bus.publish(channel, envelope)
