# 05 — Backend Architecture

## 5.1 Layering (Clean/Hexagonal-lite)

```
        ┌──────────────────────────────────────────────┐
        │  Interface layer                             │
        │  routers/ · ws/ · mcp tool handlers          │  ← knows HTTP/WS/JSON-RPC
        ├──────────────────────────────────────────────┤
        │  Application layer                           │
        │  services/ (use cases), orchestration,       │  ← knows workflows, transactions
        │  command dispatch, DTO mapping               │
        ├──────────────────────────────────────────────┤
        │  Domain layer  (libs/at_core)                │
        │  aggregates, value objects, events, rules    │  ← knows the business. Zero I/O.
        ├──────────────────────────────────────────────┤
        │  Infrastructure layer                        │
        │  repos/ · redis bus · http clients · chroma  │  ← knows the outside world
        └──────────────────────────────────────────────┘
```

Dependency rule: inward only. Domain defines *ports* (Protocols); infrastructure provides *adapters*.

Example port/adapter pair:
```
at_core.ports.TelemetryHistoryPort  (Protocol)
  ↑ implemented by
at_persistence.repos.TimescaleTelemetryRepo
at_twin.testing.InMemoryTelemetryRepo   (tests)
```

---

## 5.2 FastAPI application composition

**Middleware order (outermost → innermost):**
1. `TraceContextMiddleware` — generate/propagate `trace_id`, start OTel span
2. `CORSMiddleware`
3. `GZipMiddleware` (min 1 KB)
4. `RequestTimingMiddleware` — Server-Timing header + Prometheus histogram
5. `RateLimitMiddleware` — Redis token bucket by (user|ip, route class)
6. `AuthenticationMiddleware` — JWT decode → `request.state.principal`
7. `ProblemDetailsMiddleware` — catches `AppError` and unhandled → RFC 9457

**Lifespan:** create asyncpg pool → Redis pool → Chroma client → warm model registry cache →
start WS broadcaster task → (shutdown) drain WS, close pools.

**Dependency injection** (`deps.py`) — all providers are async generators:
`get_session`, `get_uow`, `get_redis`, `get_bus`, `get_principal`, `require_role(*roles)`,
`get_fleet_service`, `get_twin_query_service`, … Services receive repos, never sessions.

**Router registration:** `/api/v1` prefix, tags per domain, `responses={...}` documented for
every error code so OpenAPI is complete.

---

## 5.3 Use-case catalogue (application services)

| Use case | Service | Steps | Tx boundary |
|---|---|---|---|
| List fleet with sort/filter | `FleetService.list()` | Redis sorted-set lookup → hydrate hot hashes → fallback PG for cold → paginate | none (read) |
| Fleet summary KPIs | `FleetService.summary()` | cached JSON (2 s) else aggregate query | none |
| Engine detail | `TwinQueryService.detail()` | Redis state + PG snapshot + last prediction + open anomalies (gathered concurrently) | none |
| Sensor history | `TwinQueryService.history()` | choose raw vs `telemetry_1m` by requested span, downsample with `time_bucket_gapfill` | none |
| Timeline | `TwinQueryService.timeline()` | keyset-paginated `twin_events` | none |
| Start/pause/seek replay | `ReplayService.control()` | RBAC → validate → `XADD cmd.twin` → optimistic ack | none (async cmd) |
| Run simulation | `SimulationService.run()` | insert `simulations` row (PENDING) → `XADD cmd.twin{type:SIMULATE}` → 202 + poll/WS | single insert |
| Ask copilot | `AgentBridgeService.ask()` | ensure conversation → insert user message → insert `agent_runs` → `XADD cmd.agent` → subscribe `evt.agent.{run_id}` → stream to client → persist assistant message | two short tx |
| Approve work package | `MaintenanceService.approve()` | RBAC(planner) → status transition guard → write `maintenance_events` | one tx |
| Knowledge search | `KnowledgeService.search()` | hybrid retrieve via rag lib → resolve chunks from PG → return with highlights | none |

---

## 5.4 twin-engine internals

### 5.4.1 Main loop
```
every tick (period = 1000ms / speed):
  t0 = monotonic()
  1. advance ReplayClock → target_cycle per unit
  2. for each active twin in shard:
        rows = source.read(unit, up_to=target_cycle)      # usually 1 row
        state', events = twin.apply_telemetry(state, rows) # PURE
  3. batch-collect windows needing inference (every N cycles or on band change)
  4. await inference_client.predict_batch(windows)         # one HTTP call
  5. fold predictions back into twin states (pure)
  6. anomaly detectors over residuals (pure) → events
  7. publisher.publish(deltas)                             # coalesced
  8. writer.enqueue(telemetry rows, events, snapshots)     # batched, async flush
  9. sleep(period - (monotonic()-t0))   # drift-corrected
```
If step duration > period, the loop **skips ahead** (frame-dropping) and emits a
`twin.engine.lag` metric rather than accumulating backlog (P7).

### 5.4.2 Sharding & leadership
`SHARD_INDEX` / `SHARD_COUNT` env. Each shard takes a Redis lease
(`SET at:lock:shard:{n} NX PX 15000`, refreshed at 5 s). Loss of lease → graceful stop.
This is how NFR-1 scales linearly.

### 5.4.3 Command handling
Consumer group `twin-engine` on `cmd.twin`. Commands:
`START, PAUSE, RESUME, SEEK{cycle}, SET_SPEED{x}, RESET, INJECT_FAULT{...}, SIMULATE{...},
PERFORM_MAINTENANCE{module}, RETIRE`. Each is validated against the twin's status FSM
(Doc 08 §8.3); invalid transitions produce a `twin.command.rejected` event, never an exception.

### 5.4.4 Crash recovery
On boot: load latest `twin_snapshots` per unit in shard → replay `twin_events` with `seq >
snapshot.seq` → resume. Verified by a chaos test in M11 (`kill -9` mid-stream, assert state equality).

---

## 5.5 inference service

- FastAPI, single endpoint `POST /predict/batch`, msgpack body.
- `AdaptiveBatcher`: collects requests up to 8 ms or 64 items.
- Models held in an LRU of TorchScript modules keyed by `model_id`; `torch.set_num_threads(2)`,
  `inference_mode()`, channels-last irrelevant (1D), CPU by default, CUDA if available.
- Explanations are **opt-in per request** (`explain: bool`) because IG costs ~50× a forward pass;
  twin-engine requests explanations every 10 cycles or on band change, not every tick.
- Circuit breaker on the caller side: 5 consecutive failures → open 30 s → twin uses last-good
  prediction with `stale: true` flag surfaced in the UI.

---

## 5.6 Error model (RFC 9457)

```json
{
  "type": "https://aerotwin.dev/errors/twin-not-running",
  "title": "Twin is not running",
  "status": 409,
  "detail": "Engine FD001-train-U27 is PAUSED; SEEK requires RUNNING or PAUSED",
  "instance": "/api/v1/engines/{id}/replay/seek",
  "code": "TWIN_INVALID_TRANSITION",
  "trace_id": "01J9...",
  "errors": [{"field": "cycle", "message": "must be <= total_cycles"}]
}
```
Full code enumeration in Doc 12 §12.9. Frontend maps `code` → user-facing copy + recovery action.

---

## 5.7 Observability

**Metrics (Prometheus):**
`at_tick_duration_seconds{shard}`, `at_twins_active`, `at_inference_latency_seconds{model}`,
`at_ws_connections`, `at_ws_messages_sent_total{channel}`, `at_ws_dropped_frames_total`,
`at_agent_run_duration_seconds{intent}`, `at_agent_tokens_total{provider}`,
`at_tool_call_duration_seconds{server,tool}`, `at_http_request_duration_seconds{route,status}`.

**Traces:** one trace spans `HTTP ask → agent run → each node → each tool call → DB`. The Copilot
UI shows the trace tree — this is a standout demo feature.

**Health endpoints:** `/health/live`, `/health/ready` (checks PG, Redis, inference, chroma),
`/health/deep` (admin only, includes shard leases and model registry state).

---

## 5.8 Background workers

| Worker | Schedule | Job |
|---|---|---|
| `fleet_aggregator` | 1 s | Recompute fleet sorted sets + summary cache |
| `auto_health_agent_trigger` | on `twin.health.band_changed` to WARNING/CRITICAL | Enqueue Health Monitoring agent run |
| `snapshot_compactor` | 5 min | Compact events older than last snapshot |
| `prediction_downsampler` | hourly | Thin `predictions` older than 7 d |
| `rag_reindexer` | manual/CI | Rebuild Chroma from `doc_chunks` |
| `model_promoter` | manual | Promote registry stage with metric gate check |

Implementation: `arq` (Redis-backed) — lighter than Celery, async-native.

---

## 5.9 Testing strategy

| Level | Scope | Tooling | Gate |
|---|---|---|---|
| Unit | domain, twin transitions, physics, health, scoring | pytest + Hypothesis | 100 % of `at_core`, `at_twin.twin/physics/health` |
| Integration | repos, bus, API routes | testcontainers (PG+Timescale, Redis) | ≥ 80 % |
| Contract | OpenAPI ↔ generated client; MCP tool schemas | schemathesis, jsonschema | must pass |
| Simulation | 8-hour replay compressed, assert no state corruption | custom harness | must pass |
| Agent evals | 40 golden questions, faithfulness/citation precision | ragas + custom | ≥ thresholds NFR-7 |
| Load | 260 twins, 50 WS clients | locust + k6 | NFR-1/3 |
| Chaos | kill twin-engine, Redis flap, inference 500s | toxiproxy | recovery < 10 s |
| E2E | 8 user journeys | Playwright | must pass |
