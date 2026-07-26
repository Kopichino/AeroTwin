import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServerFrame } from '@/lib/types';
import { WsClient } from '@/lib/ws-client';

/** Minimal controllable WebSocket double. */
class FakeSocket {
  static instances: FakeSocket[] = [];

  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  emit(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  emitRaw(data: string): void {
    this.onmessage?.({ data });
  }

  get parsedSends(): Record<string, unknown>[] {
    return this.sent.map((item) => JSON.parse(item) as Record<string, unknown>);
  }
}

// Flush synchronously so tests never wait on a real animation frame.
const immediate = (callback: () => void) => callback();

function makeClient(): { client: WsClient; frames: ServerFrame[] } {
  const frames: ServerFrame[] = [];
  const client = new WsClient({
    url: 'ws://test/ws',
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    scheduler: immediate,
  });
  client.onFrame((frame) => frames.push(frame));
  return { client, frames };
}

function latest(): FakeSocket {
  const socket = FakeSocket.instances.at(-1);
  if (!socket) throw new Error('no socket was created');
  return socket;
}

describe('WsClient', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useRealTimers();
  });

  it('reports open once the socket connects', () => {
    const { client } = makeClient();
    client.connect();
    expect(client.status).toBe('connecting');
    latest().open();
    expect(client.status).toBe('open');
  });

  it('delivers parsed frames to handlers', () => {
    const { client, frames } = makeClient();
    client.connect();
    latest().open();
    latest().emit({ type: 'fleet.delta', payload: { engines: 3 } });

    expect(frames).toHaveLength(1);
    expect(frames[0]?.type).toBe('fleet.delta');
  });

  it('drops malformed frames without breaking the stream', () => {
    const { client, frames } = makeClient();
    client.connect();
    latest().open();

    latest().emitRaw('{not valid json');
    latest().emit({ type: 'fleet.delta', payload: {} });

    expect(frames).toHaveLength(1);
  });

  it('batches frames arriving between flushes into one pass', () => {
    const scheduled: (() => void)[] = [];
    const client = new WsClient({
      url: 'ws://test/ws',
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      scheduler: (callback) => scheduled.push(callback),
    });
    const frames: ServerFrame[] = [];
    client.onFrame((frame) => frames.push(frame));

    client.connect();
    latest().open();
    for (let index = 0; index < 20; index += 1) {
      latest().emit({ type: 'fleet.delta', payload: { index } });
    }

    // One flush is scheduled for the whole burst, not twenty.
    expect(scheduled).toHaveLength(1);
    expect(frames).toHaveLength(0);
    scheduled[0]?.();
    expect(frames).toHaveLength(20);
  });

  it('subscribes on connect and refcounts duplicate subscriptions', () => {
    const { client } = makeClient();
    client.connect();
    latest().open();

    const first = client.subscribe('fleet');
    const second = client.subscribe('fleet');
    expect(client.subscribedChannels).toEqual(['fleet']);

    const subscribes = latest().parsedSends.filter((m) => m.type === 'subscribe');
    expect(subscribes).toHaveLength(1);

    // The channel survives until the last subscriber releases it.
    first();
    expect(client.subscribedChannels).toEqual(['fleet']);
    second();
    expect(client.subscribedChannels).toEqual([]);
  });

  it('queues subscriptions made before the socket opens', () => {
    const { client } = makeClient();
    client.subscribe('fleet');
    client.connect();
    latest().open();

    const subscribes = latest().parsedSends.filter((m) => m.type === 'subscribe');
    expect(subscribes[0]?.channels).toEqual(['fleet']);
  });

  it('re-subscribes after a reconnect so the view cannot go stale', async () => {
    vi.useFakeTimers();
    const { client } = makeClient();
    client.subscribe('fleet');
    client.subscribe('twin:abc');
    client.connect();
    latest().open();

    latest().close();
    expect(client.status).toBe('reconnecting');

    await vi.advanceTimersByTimeAsync(2_000);
    latest().open();

    const subscribes = latest().parsedSends.filter((m) => m.type === 'subscribe');
    expect(subscribes[0]?.channels).toEqual(['fleet', 'twin:abc']);
    vi.useRealTimers();
  });

  it('backs off between reconnect attempts', async () => {
    vi.useFakeTimers();
    const { client } = makeClient();
    client.connect();
    latest().open();

    latest().close();
    const afterFirst = FakeSocket.instances.length;

    // Too soon for the next attempt.
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeSocket.instances.length).toBe(afterFirst);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(FakeSocket.instances.length).toBeGreaterThan(afterFirst);
    vi.useRealTimers();
  });

  it('does not reconnect after an explicit disconnect', async () => {
    vi.useFakeTimers();
    const { client } = makeClient();
    client.connect();
    latest().open();

    const count = FakeSocket.instances.length;
    client.disconnect();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(FakeSocket.instances.length).toBe(count);
    expect(client.status).toBe('closed');
    vi.useRealTimers();
  });

  it('sends heartbeats while open', async () => {
    vi.useFakeTimers();
    const { client } = makeClient();
    client.connect();
    latest().open();

    await vi.advanceTimersByTimeAsync(16_000);
    expect(latest().parsedSends.some((m) => m.type === 'ping')).toBe(true);

    client.disconnect();
    vi.useRealTimers();
  });

  it('notifies status handlers immediately on registration', () => {
    const { client } = makeClient();
    const seen: string[] = [];
    client.onStatus((status) => seen.push(status));
    expect(seen).toEqual(['closed']);
  });

  it('survives a socket factory that throws', async () => {
    vi.useFakeTimers();
    const client = new WsClient({
      url: 'ws://test/ws',
      socketFactory: () => {
        throw new Error('blocked');
      },
      scheduler: immediate,
    });
    expect(() => client.connect()).not.toThrow();
    expect(client.status).toBe('reconnecting');
    client.disconnect();
    vi.useRealTimers();
  });

  it('counts received frames', () => {
    const { client } = makeClient();
    client.connect();
    latest().open();
    latest().emit({ type: 'fleet.delta', payload: {} });
    latest().emit({ type: 'system.status', payload: {} });
    expect(client.framesReceived).toBe(2);
  });
});
