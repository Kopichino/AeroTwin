# 13 — WebSocket Communication Plan

## 13.1 Goals
One multiplexed socket per browser tab · typed envelopes shared between Python and TypeScript ·
strict backpressure (P7) · reliable reconnect with resync · zero secrets in URLs.

Endpoint: `WSS /ws/v1` (single). Channels are multiplexed inside the connection.

---

## 13.2 Envelope

```ts
type ServerFrame = {
  v: 1;
  id: string;              // ULID, per frame
  ts: string;              // ISO-8601 UTC
  ch: string;              // channel, e.g. "twin:018f..."
  seq: number;             // per-channel monotonic
  type: FrameType;
  payload: unknown;        // discriminated by `type`
  trace_id?: string;
};

type ClientFrame =
  | { type: 'auth'; ticket: string }
  | { type: 'subscribe'; channels: string[]; since?: Record<string, number> }
  | { type: 'unsubscribe'; channels: string[] }
  | { type: 'resume'; last_seq: Record<string, number> }
  | { type: 'ping'; seq: number }
  | { type: 'set_rate'; channel: string; max_hz: number };   // client-driven throttle
```

Schemas live in `libs/at_contracts` (Pydantic) and are code-generated into
`packages/ws-protocol` (Zod). Every inbound frame is Zod-parsed on the client; parse failure →
logged + dropped, never a crash.

---

## 13.3 Channels

| Channel | Payload types | Rate | Auth |
|---|---|---|---|
| `fleet` | `fleet.snapshot`, `fleet.delta`, `fleet.alert` | 1 Hz | viewer+ |
| `twin:{engine_id}` | `twin.snapshot`, `twin.delta`, `twin.event`, `twin.prediction`, `twin.anomaly` | ≤ 4 Hz | viewer+ |
| `twin:{engine_id}:sensors` | `sensor.frame` (high-rate raw values) | ≤ 10 Hz, opt-in | viewer+ |
| `agent:{run_id}` | `agent.step`, `agent.tool_call`, `agent.token`, `agent.completed`, `agent.error` | burst | owner/admin |
| `sim:{simulation_id}` | `sim.progress`, `sim.completed`, `sim.failed` | ≤ 2 Hz | owner |
| `notifications` | `notification` (band changes, new work packages, drift) | event | viewer+ |
| `system` | `system.status`, `replay.status`, `degraded.mode` | 0.2 Hz | viewer+ |

Subscribing to `twin:{id}` returns a **full snapshot first**, then deltas. This removes any
ordering/gap race for a newly opened engine page.

---

## 13.4 Message catalogue (payload shapes)

```jsonc
// twin.delta — coalesced; only changed fields present
{ "engine_id":"018f...", "cycle":178,
  "hi":34.1, "band":"CRITICAL",
  "components":{"HPC":41.0},
  "rul":{"p50":19.4,"p10":11.2,"p90":31.8,"stale":false},
  "anomaly":{"score":3.4,"module":"HPC"},
  "sensors":{"s3":1591.2,"s8":9046.1,"s11":47.2},   // subset: charted + fan RPM only
  "regime":0 }

// fleet.delta — only engines whose ranked fields changed
{ "changed":[{"engine_id":"018f...","hi":34.1,"band":"CRITICAL","rul_p50":19.4,"priority":0.87}],
  "counts":{"HEALTHY":142,"WATCH":61,"WARNING":38,"CRITICAL":19},
  "avg_health":68.4 }

// agent.token
{ "run_id":"018f...","index":42,"delta":" the HPC efficiency" }

// agent.tool_call
{ "run_id":"...","step":3,"server":"twin","tool":"get_sensor_history",
  "args_preview":{"sensors":["s3","s11"],"from_cycle":118},"duration_ms":41,"ok":true }

// notification
{ "severity":"CRITICAL","title":"Engine FD001-U27 entered CRITICAL",
  "engine_id":"018f...","action":{"label":"View diagnosis","href":"/engines/018f.../timeline"} }
```

---

## 13.5 Backpressure & coalescing (P7)

**Server side, three layers:**
1. **Producer coalescing** (twin-engine): per-engine delta accumulator flushed at ≤ 4 Hz; newer
   values overwrite older ones in the same window. Only *changed* fields are emitted (dirty-field set).
2. **Per-connection outbound queue**: bounded at 256 frames. On overflow, policy per channel:
   - `twin.delta`, `fleet.delta`, `sensor.frame` → **drop oldest** (state-replacing, safe to lose)
   - `agent.token`, `twin.event`, `notification` → **must deliver**; if the queue is full of these,
     the connection is marked `slow` and receives a `resync` instruction instead.
3. **Client-declared rate**: `set_rate` lets a background tab drop to 0.5 Hz; the frontend
   automatically sends this on `visibilitychange`.

**Client side:** frames are drained in a `requestAnimationFrame` batch, so at most one store commit
per painted frame regardless of arrival rate.

Metrics: `at_ws_dropped_frames_total{channel,reason}`, `at_ws_queue_depth`, `at_ws_slow_clients`.

---

## 13.6 Connection lifecycle

| Phase | Behaviour |
|---|---|
| Ticket | `POST /ws/ticket` → 30 s single-use ticket (never a JWT in the URL — it would land in logs) |
| Handshake | First client frame must be `auth` within 5 s, else close `4401` |
| Heartbeat | Client `ping` every 15 s; server closes at 45 s silence (`4408`). Server also sends its own keepalive for proxies |
| Subscribe | Idempotent; server returns `subscribed` + snapshot per channel |
| Reconnect | Exponential backoff `0.5,1,2,4,8 s` ±30 % jitter, capped, max 10 attempts then manual retry UI |
| Resume | Client sends `resume {last_seq}`. Server compares; if the gap is small and the channel is event-typed, it replays from a short Redis buffer; otherwise it sends `resync` + fresh snapshot |
| Close codes | `4400` bad frame · `4401` auth failed · `4403` forbidden channel · `4408` heartbeat timeout · `4429` too many connections (max 3/user) · `1012` server restarting (client reconnects immediately) |

**Gap detection:** the client tracks `seq` per channel; a gap triggers an automatic snapshot request
for that channel only. Correctness never depends on lossless delivery of deltas.

---

## 13.7 Scaling & fan-out
`api` instances each subscribe to Redis Pub/Sub patterns and route to their own local connections.
A single Redis message therefore fans out to N api instances × M local clients — no N² traffic.
Per-engine channels use exact keys (`evt.twin.{id}`), so an api instance with no subscribers for
an engine simply doesn't subscribe (dynamic SUBSCRIBE/UNSUBSCRIBE keyed by local refcount).

---

## 13.8 Security
- Ticket single-use, 30 s TTL, bound to user + IP + user-agent hash.
- Channel authorization on subscribe (`agent:{run_id}` and `sim:{id}` require ownership).
- Inbound frame size cap 8 KB, rate cap 20 client frames/s → close `4429`.
- Origin check on upgrade; WSS only in non-dev.
- No mutating operations over WS — commands go through REST. The socket is **read-mostly** by design
  (simpler auth, simpler audit).

---

## 13.9 Testing
`pytest-asyncio` WS client tests for handshake, auth failure, subscribe/snapshot, gap→resync,
heartbeat timeout, backpressure drop policy · a mock WS server for frontend tests ·
a load test with 50 clients × 260 engines asserting NFR-3 and zero must-deliver drops.
