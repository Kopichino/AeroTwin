"""Redis adapter for the event and command buses (ADR-002).

Satisfies the same ports as the in-memory adapter, so switching is a single line
in the composition root:

    bus = RedisBus(settings.redis_dsn) if settings.use_redis else InMemoryBus()

Design notes:

* **Deltas use pub/sub, not streams.** Twin deltas are state-replacing and
  worthless once superseded; persisting them would cost writes for data nobody
  replays. Durability lives in Postgres, not the bus.
* **Commands use Streams with a consumer group.** Commands must not be lost and
  must be processed exactly once per group, which is precisely what
  ``XREADGROUP`` plus ``XACK`` provides. ``XAUTOCLAIM`` recovers messages
  stranded by a crashed consumer.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from at_bus.ports import Envelope, EventBus

STREAM_COMMANDS = "cmd.twin"
MAX_STREAM_LENGTH = 100_000
CLAIM_IDLE_MS = 30_000


class RedisBus(EventBus):
    """Pub/sub event fan-out backed by Redis."""

    def __init__(self, dsn: str, *, namespace: str = "at:local") -> None:
        self._dsn = dsn
        self._namespace = namespace
        self._client: Any = None
        self.published = 0

    def _key(self, channel: str) -> str:
        return f"{self._namespace}:ps:{channel}"

    async def _connect(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._dsn, decode_responses=True)
        return self._client

    async def publish(self, channel: str, envelope: Envelope) -> None:
        client = await self._connect()
        self.published += 1
        await client.publish(self._key(channel), json.dumps(envelope.to_dict()))

    async def subscribe(self, channel: str) -> AsyncIterator[Envelope]:
        client = await self._connect()
        pubsub = client.pubsub()
        await pubsub.subscribe(self._key(channel))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                    yield Envelope.from_dict(json.loads(message["data"]))
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(self._key(channel))
                await pubsub.aclose()

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None


class RedisCommandBus:
    """Durable command delivery over a Redis Stream consumer group."""

    def __init__(self, dsn: str, *, namespace: str = "at:local") -> None:
        self._dsn = dsn
        self._namespace = namespace
        self._client: Any = None
        self.sent = 0

    @property
    def _stream(self) -> str:
        return f"{self._namespace}:stream:{STREAM_COMMANDS}"

    async def _connect(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._dsn, decode_responses=True)
        return self._client

    async def send(self, envelope: Envelope) -> None:
        client = await self._connect()
        self.sent += 1
        await client.xadd(
            self._stream,
            {"data": json.dumps(envelope.to_dict())},
            maxlen=MAX_STREAM_LENGTH,
            approximate=True,
        )

    async def ensure_group(self, group: str) -> None:
        """Create the consumer group, tolerating an existing one."""
        client = await self._connect()
        with contextlib.suppress(Exception):
            await client.xgroup_create(self._stream, group, id="0", mkstream=True)

    async def consume(
        self, group: str = "twin-engine", consumer: str = "0"
    ) -> AsyncIterator[Envelope]:
        client = await self._connect()
        await self.ensure_group(group)

        while True:
            # Reclaim anything a crashed consumer left unacknowledged before
            # taking new work, so a restart never silently drops commands.
            with contextlib.suppress(Exception):
                _, claimed, _ = await client.xautoclaim(
                    self._stream, group, consumer, min_idle_time=CLAIM_IDLE_MS, count=10
                )
                for message_id, fields in claimed:
                    envelope = _decode(fields)
                    if envelope is not None:
                        yield envelope
                    await client.xack(self._stream, group, message_id)

            response = await client.xreadgroup(
                group, consumer, {self._stream: ">"}, count=32, block=1000
            )
            if not response:
                continue
            for _stream_name, messages in response:
                for message_id, fields in messages:
                    envelope = _decode(fields)
                    if envelope is not None:
                        yield envelope
                    await client.xack(self._stream, group, message_id)

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None


def _decode(fields: dict[str, str]) -> Envelope | None:
    raw = fields.get("data")
    if not raw:
        return None
    try:
        return Envelope.from_dict(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
