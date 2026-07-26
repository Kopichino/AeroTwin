/**
 * Multiplexed WebSocket client (Doc 13).
 *
 * One socket per tab. Channels are refcounted so two components watching the
 * same engine share a single subscription, and the channel is only dropped when
 * the last of them unmounts.
 *
 * Two behaviours matter more than the plumbing:
 *
 * - **Frames are batched into an animation frame.** The server can emit several
 *   hundred frames per second across a 260-engine fleet. Committing each one to
 *   React state individually would re-render faster than the browser can paint.
 * - **Reconnect is expected, not exceptional.** Backoff is exponential with
 *   jitter, and every reconnect re-subscribes and requests a fresh snapshot, so
 *   a dropped connection self-heals without the UI showing stale numbers.
 */

import type { ConnectionStatus, ServerFrame } from './types';

export type FrameHandler = (frame: ServerFrame) => void;
export type StatusHandler = (status: ConnectionStatus, attempt: number) => void;

const HEARTBEAT_MS = 15_000;
const BACKOFF_STEPS_MS = [500, 1_000, 2_000, 4_000, 8_000];
const MAX_ATTEMPTS = 12;

export interface WsClientOptions {
  url?: string;
  /** Injectable for tests; defaults to the platform WebSocket. */
  socketFactory?: (url: string) => WebSocket;
  /** Injectable so tests can flush synchronously instead of waiting for paint. */
  scheduler?: (callback: () => void) => void;
}

function defaultUrl(): string {
  // Next.js `rewrites` proxy HTTP but **not** websocket upgrades, so the socket
  // must address the API origin directly rather than going through the dev
  // server. Verified: a connection to the Next origin times out during the
  // opening handshake, while the same request to the API origin succeeds.
  //
  // In production both sit behind one reverse proxy (Caddy, Doc 01 section 1.9),
  // which does handle upgrades, so NEXT_PUBLIC_WS_URL is left unset there and
  // the same-origin path below applies.
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured) return configured;

  if (typeof window === 'undefined') return 'ws://localhost:8000/ws/v1';
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${protocol}://${window.location.host}/ws/v1`;
}

function defaultScheduler(callback: () => void): void {
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(callback);
  else setTimeout(callback, 16);
}

export class WsClient {
  private socket: WebSocket | null = null;
  private readonly url: string;
  private readonly makeSocket: (url: string) => WebSocket;
  private readonly schedule: (callback: () => void) => void;

  private readonly channels = new Map<string, number>();
  private readonly frameHandlers = new Set<FrameHandler>();
  private readonly statusHandlers = new Set<StatusHandler>();

  private queue: ServerFrame[] = [];
  private flushScheduled = false;

  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private closedByUser = false;

  status: ConnectionStatus = 'closed';
  framesReceived = 0;

  constructor(options: WsClientOptions = {}) {
    this.url = options.url ?? defaultUrl();
    this.makeSocket = options.socketFactory ?? ((url) => new WebSocket(url));
    this.schedule = options.scheduler ?? defaultScheduler;
  }

  connect(): void {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    this.closedByUser = false;
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting');

    let socket: WebSocket;
    try {
      socket = this.makeSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus('open');
      // Re-subscribe on every open. The server answers each subscribe with a
      // snapshot, so a reconnect cannot leave the UI showing stale state.
      const channels = [...this.channels.keys()];
      if (channels.length > 0) this.send({ type: 'subscribe', channels });
      this.startHeartbeat();
    };

    socket.onmessage = (event) => {
      let frame: ServerFrame;
      try {
        frame = JSON.parse(event.data as string) as ServerFrame;
      } catch {
        return; // A malformed frame is dropped, never fatal.
      }
      this.framesReceived += 1;
      this.queue.push(frame);
      this.scheduleFlush();
    };

    socket.onerror = () => {
      /* onclose always follows; recovery is handled there. */
    };

    socket.onclose = () => {
      this.stopHeartbeat();
      this.socket = null;
      if (!this.closedByUser) this.scheduleReconnect();
      else this.setStatus('closed');
    };
  }

  disconnect(): void {
    this.closedByUser = true;
    this.stopHeartbeat();
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.socket?.close();
    this.socket = null;
    this.setStatus('closed');
  }

  /** Subscribe to a channel; returns an unsubscribe function. */
  subscribe(channel: string): () => void {
    const count = this.channels.get(channel) ?? 0;
    this.channels.set(channel, count + 1);
    if (count === 0 && this.isOpen) this.send({ type: 'subscribe', channels: [channel] });

    return () => {
      const current = this.channels.get(channel) ?? 0;
      if (current <= 1) {
        this.channels.delete(channel);
        if (this.isOpen) this.send({ type: 'unsubscribe', channels: [channel] });
      } else {
        this.channels.set(channel, current - 1);
      }
    };
  }

  onFrame(handler: FrameHandler): () => void {
    this.frameHandlers.add(handler);
    return () => this.frameHandlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this.status, this.attempt);
    return () => this.statusHandlers.delete(handler);
  }

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  get subscribedChannels(): string[] {
    return [...this.channels.keys()];
  }

  private send(message: unknown): void {
    if (this.isOpen) this.socket?.send(JSON.stringify(message));
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    for (const handler of this.statusHandlers) handler(status, this.attempt);
  }

  private scheduleFlush(): void {
    if (this.flushScheduled) return;
    this.flushScheduled = true;
    this.schedule(() => {
      this.flushScheduled = false;
      const batch = this.queue;
      this.queue = [];
      for (const frame of batch) {
        for (const handler of this.frameHandlers) handler(frame);
      }
    });
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeat = setInterval(() => this.send({ type: 'ping', seq: Date.now() }), HEARTBEAT_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  private scheduleReconnect(): void {
    if (this.attempt >= MAX_ATTEMPTS) {
      this.setStatus('closed');
      return;
    }
    const step = BACKOFF_STEPS_MS[Math.min(this.attempt, BACKOFF_STEPS_MS.length - 1)] ?? 8_000;
    // Jitter prevents every client in a fleet reconnecting in lockstep and
    // hammering the server the instant it comes back up.
    const delay = step * (0.7 + Math.random() * 0.6);
    this.attempt += 1;
    this.setStatus('reconnecting');
    this.retry = setTimeout(() => this.connect(), delay);
  }
}

let singleton: WsClient | null = null;

/** Process-wide client, so every hook shares one socket. */
export function getWsClient(): WsClient {
  if (!singleton) singleton = new WsClient();
  return singleton;
}

export function resetWsClient(): void {
  singleton?.disconnect();
  singleton = null;
}
