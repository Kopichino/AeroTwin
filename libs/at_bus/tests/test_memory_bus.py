"""Tests for the in-process bus, especially the backpressure policy (Doc 13 section 13.5)."""

from __future__ import annotations

import asyncio

from at_bus import Envelope, InMemoryBus, InMemoryCommandBus, twin_channel
from at_bus.memory import REPLACEABLE_TYPES


async def collect(
    bus: InMemoryBus, channel: str, count: int
) -> tuple[list[Envelope], asyncio.Task[None]]:
    """Start a subscriber and return its buffer plus the reader task."""
    received: list[Envelope] = []

    async def reader() -> None:
        async for envelope in bus.subscribe(channel):
            received.append(envelope)
            if len(received) >= count:
                return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.01)
    return received, task


# ── envelopes ────────────────────────────────────────────────────────────────


def test_envelope_round_trips_through_json() -> None:
    original = Envelope(type="twin.delta", payload={"hi": 42.5}, channel="twin:abc", trace_id="t-1")
    restored = Envelope.from_dict(original.to_dict())
    assert restored.type == original.type
    assert restored.payload == original.payload
    assert restored.trace_id == "t-1"
    assert restored.id == original.id


def test_envelope_tolerates_missing_fields() -> None:
    restored = Envelope.from_dict({"type": "x"})
    assert restored.type == "x"
    assert restored.payload == {}


def test_channel_helpers() -> None:
    assert twin_channel("abc") == "twin:abc"


# ── publish / subscribe ──────────────────────────────────────────────────────


async def test_subscriber_receives_published_envelopes() -> None:
    bus = InMemoryBus()
    received, task = await collect(bus, "fleet", 3)
    for index in range(3):
        await bus.publish("fleet", Envelope(type="fleet.delta", payload={"i": index}))
    await asyncio.wait_for(task, 2)
    assert [item.payload["i"] for item in received] == [0, 1, 2]
    await bus.close()


async def test_publish_stamps_the_channel() -> None:
    bus = InMemoryBus()
    received, task = await collect(bus, "fleet", 1)
    await bus.publish("fleet", Envelope(type="fleet.delta", payload={}))
    await asyncio.wait_for(task, 2)
    assert received[0].channel == "fleet"
    await bus.close()


async def test_channels_are_isolated() -> None:
    bus = InMemoryBus()
    received, task = await collect(bus, "twin:a", 1)
    await bus.publish("twin:b", Envelope(type="twin.delta", payload={"wrong": True}))
    await bus.publish("twin:a", Envelope(type="twin.delta", payload={"right": True}))
    await asyncio.wait_for(task, 2)
    assert received[0].payload == {"right": True}
    await bus.close()


async def test_multiple_subscribers_all_receive() -> None:
    bus = InMemoryBus()
    first, task_a = await collect(bus, "fleet", 1)
    second, task_b = await collect(bus, "fleet", 1)
    assert bus.subscriber_count("fleet") == 2
    await bus.publish("fleet", Envelope(type="fleet.delta", payload={"n": 1}))
    await asyncio.wait_for(asyncio.gather(task_a, task_b), 2)
    assert first[0].payload == second[0].payload == {"n": 1}
    await bus.close()


async def test_publish_without_subscribers_is_harmless() -> None:
    bus = InMemoryBus()
    await bus.publish("nobody", Envelope(type="twin.delta", payload={}))
    assert bus.published == 1
    await bus.close()


async def test_subscriber_is_removed_on_exit() -> None:
    bus = InMemoryBus()

    async def brief() -> None:
        async for _ in bus.subscribe("fleet"):
            return

    task = asyncio.create_task(brief())
    await asyncio.sleep(0.01)
    await bus.publish("fleet", Envelope(type="fleet.delta", payload={}))
    await asyncio.wait_for(task, 2)
    # The generator's finally block runs on the next event-loop pass.
    await asyncio.sleep(0.01)
    assert bus.subscriber_count("fleet") == 0
    await bus.close()


async def test_close_wakes_pending_subscribers() -> None:
    bus = InMemoryBus()
    done = asyncio.Event()

    async def reader() -> None:
        async for _ in bus.subscribe("fleet"):
            pass
        done.set()

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.01)
    await bus.close()
    await asyncio.wait_for(done.wait(), 2)
    task.cancel()


# ── backpressure (P7) ────────────────────────────────────────────────────────


async def test_slow_subscriber_sheds_stale_state_frames() -> None:
    """A slow browser tab must never stall the twin engine."""
    bus = InMemoryBus(queue_size=8)

    async def stalled() -> None:
        async for _ in bus.subscribe("fleet"):
            await asyncio.sleep(10)  # never keeps up

    task = asyncio.create_task(stalled())
    await asyncio.sleep(0.01)

    for index in range(200):
        await bus.publish("fleet", Envelope(type="fleet.delta", payload={"i": index}))

    assert bus.published == 200
    assert bus.dropped_frames > 0, "backpressure should have shed frames"
    task.cancel()
    await bus.close()


async def test_dropped_frames_are_the_oldest_not_the_newest() -> None:
    """Freshness matters more than completeness for state-replacing frames."""
    bus = InMemoryBus(queue_size=4)
    seen: list[int] = []
    subscribed = asyncio.Event()

    async def slow() -> None:
        iterator = bus.subscribe("fleet")
        subscribed.set()
        # Stall so the mailbox overflows while frames are being published.
        await asyncio.sleep(0.05)
        async for envelope in iterator:
            seen.append(envelope.payload["i"])

    task = asyncio.create_task(slow())
    await subscribed.wait()
    await asyncio.sleep(0.01)

    for index in range(50):
        await bus.publish("fleet", Envelope(type="fleet.delta", payload={"i": index}))
    await asyncio.sleep(0.15)
    task.cancel()

    assert seen, "subscriber should have received something"
    assert max(seen) > 40, "the newest frames must survive"
    await bus.close()


async def test_must_deliver_frames_survive_a_flood_of_state_frames() -> None:
    """Events and alerts are not replaceable and must not be silently dropped.

    Note the ``subscribed`` event: creating the async generator is not enough to
    register the subscription, the first ``__anext__`` is. An earlier version of
    this test slept before iterating and therefore published into the void.
    """
    bus = InMemoryBus(queue_size=8)
    received: list[Envelope] = []
    subscribed = asyncio.Event()

    async def slow() -> None:
        iterator = bus.subscribe("fleet")
        subscribed.set()
        await asyncio.sleep(0.05)
        async for envelope in iterator:
            received.append(envelope)

    task = asyncio.create_task(slow())
    await subscribed.wait()
    await asyncio.sleep(0.01)

    for index in range(40):
        await bus.publish("fleet", Envelope(type="fleet.delta", payload={"i": index}))
    await bus.publish("fleet", Envelope(type="twin.event", payload={"critical": True}))

    await asyncio.sleep(0.2)
    task.cancel()

    assert any(item.type == "twin.event" for item in received), (
        "a must-deliver frame was dropped under backpressure"
    )
    await bus.close()


def test_replaceable_type_registry_is_explicit() -> None:
    assert "twin.delta" in REPLACEABLE_TYPES
    assert "twin.event" not in REPLACEABLE_TYPES


# ── command bus ──────────────────────────────────────────────────────────────


async def test_commands_are_delivered_in_order() -> None:
    commands = InMemoryCommandBus()
    for index in range(5):
        await commands.send(Envelope(type="cmd", payload={"i": index}))
    drained = commands.drain_nowait()
    assert [item.payload["i"] for item in drained] == [0, 1, 2, 3, 4]


async def test_drain_on_empty_queue_returns_empty() -> None:
    assert InMemoryCommandBus().drain_nowait() == []


async def test_closed_command_bus_rejects_sends() -> None:
    commands = InMemoryCommandBus()
    await commands.close()
    await commands.send(Envelope(type="cmd", payload={}))
    assert commands.sent == 0
