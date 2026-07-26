"""In-process event bus adapter.

Implements the same ports as the Redis adapter using ``asyncio`` primitives, so
the whole platform runs in a single process with no external services. This is
what makes the standalone demo possible and keeps the streaming path fully
testable without containers.

Backpressure policy (Doc 13 section 13.5) is implemented here rather than left to
the caller: state-replacing frames are dropped oldest-first when a subscriber
falls behind, while must-deliver frames block briefly and are only dropped as a
last resort. A slow browser tab must never be able to stall the twin engine.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator

from at_bus.ports import Envelope, EventBus

#: Frame types that carry replaceable state. Losing an intermediate one is
#: harmless because the next frame supersedes it.
REPLACEABLE_TYPES: frozenset[str] = frozenset(
    {"twin.delta", "fleet.delta", "sensor.frame", "system.status"}
)

DEFAULT_QUEUE_SIZE = 256


class _Subscription:
    """One subscriber's bounded mailbox."""

    __slots__ = ("channel", "dropped", "queue")

    def __init__(self, channel: str, maxsize: int) -> None:
        self.channel = channel
        self.queue: asyncio.Queue[Envelope | None] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, envelope: Envelope) -> None:
        """Enqueue without blocking the publisher.

        Replaceable frames are dropped oldest-first so a slow consumer sees the
        freshest state rather than a stale backlog. Must-deliver frames evict a
        replaceable frame if one is queued, and are only dropped if the mailbox
        is entirely full of must-deliver traffic.
        """
        try:
            self.queue.put_nowait(envelope)
            return
        except asyncio.QueueFull:
            pass

        if envelope.type in REPLACEABLE_TYPES:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
                self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(envelope)
            return

        # Must-deliver frame into a full mailbox: drain, drop the oldest
        # replaceable frames to make room, then requeue preserving order.
        #
        # The new frame is appended to the rebuilt list rather than pushed after
        # requeuing, because requeuing can refill the queue and reject it -- the
        # exact bug this comment exists to prevent regressing.
        buffered: list[Envelope] = []
        while not self.queue.empty():
            item = self.queue.get_nowait()
            if item is not None:
                buffered.append(item)

        buffered.append(envelope)

        # Shed replaceable frames, oldest first, until the batch fits.
        while len(buffered) > self.queue.maxsize:
            index = next(
                (i for i, item in enumerate(buffered) if item.type in REPLACEABLE_TYPES),
                None,
            )
            if index is None:
                # Nothing replaceable left: the mailbox is entirely must-deliver
                # traffic, so shed the oldest of those instead of the new one.
                index = 0
            buffered.pop(index)
            self.dropped += 1

        for item in buffered:
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(item)

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)


class InMemoryBus(EventBus):
    """Single-process pub/sub with per-subscriber backpressure."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subs: dict[str, set[_Subscription]] = defaultdict(set)
        self._closed = False
        self.published = 0

    @property
    def dropped_frames(self) -> int:
        """Total frames shed under backpressure, exported as a metric."""
        return sum(sub.dropped for subs in self._subs.values() for sub in subs)

    def subscriber_count(self, channel: str) -> int:
        return len(self._subs.get(channel, ()))

    async def publish(self, channel: str, envelope: Envelope) -> None:
        if self._closed:
            return
        self.published += 1
        stamped = envelope if envelope.channel == channel else _rechannel(envelope, channel)
        for sub in tuple(self._subs.get(channel, ())):
            sub.offer(stamped)

    def subscribe(self, channel: str) -> AsyncIterator[Envelope]:
        """Subscribe to a channel.

        Registration happens **here**, not on first iteration. An async generator
        body does not execute until ``__anext__`` is awaited, so a naive
        implementation silently misses every frame published between calling
        ``subscribe()`` and starting to consume -- a race that is easy to hit and
        very hard to debug. Registering eagerly and returning a separate
        generator closes that window.
        """
        sub = _Subscription(channel, self._queue_size)
        self._subs[channel].add(sub)
        return self._drain(sub)

    async def _drain(self, sub: _Subscription) -> AsyncIterator[Envelope]:
        channel = sub.channel
        try:
            while True:
                item = await sub.queue.get()
                if item is None:
                    return
                yield item
        finally:
            self._subs[channel].discard(sub)
            if not self._subs[channel]:
                self._subs.pop(channel, None)

    async def close(self) -> None:
        self._closed = True
        for subs in self._subs.values():
            for sub in subs:
                sub.close()


def _rechannel(envelope: Envelope, channel: str) -> Envelope:
    from dataclasses import replace

    return replace(envelope, channel=channel)


class InMemoryCommandBus:
    """Ordered command queue with at-least-once semantics.

    Mirrors the Redis Streams consumer-group behaviour closely enough that the
    twin engine's consumption loop is identical against either adapter.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.Queue[Envelope | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self.sent = 0

    async def send(self, envelope: Envelope) -> None:
        if self._closed:
            return
        self.sent += 1
        await self._queue.put(envelope)

    async def consume(
        self, group: str = "twin-engine", consumer: str = "0"
    ) -> AsyncIterator[Envelope]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    def drain_nowait(self) -> list[Envelope]:
        """Pull every pending command without awaiting.

        The twin engine drains commands at the top of each tick rather than
        running a separate consumer task, which keeps the single-writer
        invariant trivially true.
        """
        items: list[Envelope] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return items
            if item is not None:
                items.append(item)

    async def close(self) -> None:
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
