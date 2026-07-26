# 11 — Sequence Diagrams

Twelve canonical flows. These are the contracts implementation must satisfy.

---

## 11.1 SEQ-01 — Cold start: boot & rehydrate

```mermaid
sequenceDiagram
    autonumber
    participant OP as Operator (make demo)
    participant PG as Postgres
    participant RD as Redis
    participant TE as twin-engine
    participant INF as inference
    participant API as api
    participant WEB as web

    OP->>PG: alembic upgrade head; seed engines/models/docs
    OP->>INF: start → load PRODUCTION TorchScript per subset, warmup 20 fwd passes
    INF-->>OP: /health/ready 200
    OP->>TE: start (SHARD_INDEX=0, SHARD_COUNT=1)
    TE->>RD: SET lock:shard:0 NX PX 15000
    TE->>PG: SELECT latest twin_snapshots for shard units
    TE->>PG: SELECT twin_events WHERE seq > snapshot.seq
    TE->>TE: replay events → in-memory TwinState registry
    TE->>RD: HSET twin:{id}:state (hot cache) ×N
    TE-->>OP: ready, 260 twins IDLE
    OP->>API: start
    API->>PG: pool; API->>RD: pool
    API-->>OP: /health/ready 200
    OP->>WEB: start
    WEB->>API: GET /api/v1/fleet
    API-->>WEB: 200 fleet page 1
```

---

## 11.2 SEQ-02 — Real-time tick → browser paint (the core loop)

```mermaid
sequenceDiagram
    autonumber
    participant CLK as ReplayClock
    participant TE as twin-engine
    participant INF as inference
    participant RD as Redis PubSub
    participant API as api (WS gateway)
    participant WEB as browser
    participant R3F as 3D scene

    CLK->>TE: tick (period = 1000/speed ms)
    TE->>TE: read next telemetry row per active twin
    TE->>TE: regime detect → residuals → physics proxies → component health
    par batched inference
        TE->>INF: POST /predict/batch (64 windows, msgpack)
        INF->>INF: scale → forward → MC-dropout → intervals
        INF-->>TE: rul p10/p50/p90, failure probs, [attributions]
    and anomaly detection
        TE->>TE: EWMA + CUSUM + AE recon → score, sensors, module
    end
    TE->>TE: fuse Health Index, EWMA, hysteresis → events
    TE->>RD: PUBLISH evt.twin.{id} (delta, coalesced ≤4Hz)
    TE->>RD: PUBLISH evt.fleet (rollup, 1Hz)
    TE-)PG: batched COPY telemetry/events (async, off critical path)
    RD-->>API: message
    API->>API: filter by client subscriptions, apply per-client rate cap
    API-->>WEB: WS frame {type:"twin.delta", ...}
    WEB->>WEB: Zustand slice update (no React re-render of tree)
    WEB->>R3F: uniforms: health color, emissive, fanSpeed
    R3F-->>WEB: next frame ≤16ms
```

---

## 11.3 SEQ-03 — Health band crosses to CRITICAL → autonomous agent chain

```mermaid
sequenceDiagram
    autonumber
    participant TE as twin-engine
    participant RD as Redis
    participant AR as agent-runtime
    participant MCP as MCP servers
    participant LLM as LLM
    participant PG as Postgres
    participant API as api
    participant WEB as browser

    TE->>TE: HI 36.4 → 34.1, 3 consecutive cycles → band CRITICAL latched
    TE->>RD: PUBLISH evt.twin.{id} {band_changed}
    TE->>RD: XADD cmd.agent {trigger:auto_health, engine_id, run_id}
    AR->>RD: XREADGROUP → claim
    AR->>PG: INSERT agent_runs (RUNNING)
    AR->>AR: guard_input → router (intent=HEALTH, entity=engine)
    AR->>MCP: get_twin_state, get_component_health, get_anomalies, get_prediction
    MCP-->>AR: evidence (HPC 41%, W31 drift +3.2σ, RUL p50=19)
    AR->>AR: health_agent → escalate=true
    AR->>MCP: get_sensor_history(s3,s11,s17,s20, last 60 cycles)
    AR->>LLM: diagnosis prompt + evidence
    LLM-->>AR: HPC_EFFICIENCY_LOSS, confidence 0.82
    AR->>MCP: rag_search("HPC efficiency loss borescope AMM 72-31")
    MCP-->>AR: 5 chunks + citations
    AR->>MCP: get_task_card, lookup_part, estimate_cost, get_maintenance_slots
    AR->>LLM: planning prompt
    LLM-->>AR: WorkPackage draft
    AR->>MCP: create_work_package (status DRAFT)
    MCP->>PG: INSERT work_packages + tasks
    AR->>AR: synthesizer → critic (grounded ✓ cited ✓) → guard_output
    AR->>PG: UPDATE agent_runs COMPLETED; INSERT steps/tool_calls/citations
    AR->>RD: PUBLISH evt.agent.{run_id} {completed} ; PUBLISH evt.fleet {notification}
    RD-->>API: message
    API-->>WEB: WS {type:"notification", severity:"CRITICAL", work_package_id}
    WEB->>WEB: toast + engine card badge + timeline card appears
```

---

## 11.4 SEQ-04 — Copilot question with streaming

```mermaid
sequenceDiagram
    autonumber
    actor U as Engineer
    participant WEB as browser
    participant API as api
    participant PG as Postgres
    participant RD as Redis
    participant AR as agent-runtime
    participant MCP as MCP
    participant LLM as LLM

    U->>WEB: "Why is Engine 27 unhealthy and can it fly 50 more cycles?"
    WEB->>API: POST /api/v1/copilot/ask {conversation_id, engine_id, message}
    API->>PG: INSERT messages(user), INSERT agent_runs(RUNNING)
    API->>RD: XADD cmd.agent
    API-->>WEB: 202 {run_id}
    WEB->>API: WS subscribe channel agent:{run_id}
    AR->>RD: consume
    AR->>RD: PUBLISH step.started(router)
    AR->>AR: intent = DIAGNOSIS + WHATIF (compound) → plan [diag, knowledge, sim, synth]
    loop each tool call
        AR->>MCP: tool(args)
        MCP-->>AR: result
        AR->>RD: PUBLISH tool.call {server, tool, args, ms}
        RD-->>API: → WEB (tool trace viewer updates live)
    end
    AR->>MCP: run_simulation{horizon:50, scenario: no intervention}
    MCP-->>AR: P(fail within 50) = 0.71
    AR->>LLM: stream synthesis
    loop tokens
        LLM-->>AR: delta
        AR->>RD: PUBLISH agent.token
        RD-->>API: →
        API-->>WEB: WS token
        WEB->>WEB: append to StreamingMarkdown
    end
    AR->>AR: critic pass → guard_output (citations resolve ✓)
    AR->>PG: INSERT messages(assistant), citations; UPDATE run COMPLETED
    AR->>RD: PUBLISH run.completed {confidence, followups}
    API-->>WEB: WS completed → render citation chips + "Create work package?" CTA
```

---

## 11.5 SEQ-05 — What-if simulation

```mermaid
sequenceDiagram
    autonumber
    actor U
    participant WEB
    participant API
    participant PG
    participant RD
    participant TE as twin-engine (sandbox)
    participant INF as inference

    U->>WEB: ScenarioBuilder: +8°R on T30, delay maintenance 40 cycles, horizon 100
    WEB->>API: POST /engines/{id}/simulate
    API->>API: validate ScenarioSpec (zod/pydantic), RBAC, rate limit
    API->>PG: INSERT simulations (PENDING)
    API->>RD: XADD cmd.twin {type:SIMULATE, sim_id}
    API-->>WEB: 202 {simulation_id} + subscribe sim:{id}
    TE->>TE: fork TwinState at base_cycle (sandboxed, no live writes)
    loop 50 Monte-Carlo paths × 100 cycles
        TE->>TE: extrapolate degradation + apply interventions + seeded noise
        TE->>INF: batched score
        INF-->>TE: RUL
    end
    TE->>TE: aggregate median + p10/p90, failure-cycle distribution, Δ vs baseline
    TE->>PG: UPDATE simulations DONE (baseline, scenario, delta_summary)
    TE->>RD: PUBLISH evt.sim.{id} {done}
    RD-->>API-->>WEB: WS done
    WEB->>API: GET /simulations/{id}
    API-->>WEB: full result
    WEB->>WEB: overlay baseline vs scenario + delta cards
```

---

## 11.6 SEQ-06 — WebSocket connect, auth, subscribe, reconnect

```mermaid
sequenceDiagram
    autonumber
    participant WEB
    participant API
    participant RD

    WEB->>API: POST /api/v1/ws/ticket (Bearer JWT)
    API->>RD: SET ws:ticket:{t} {user,role} EX 30
    API-->>WEB: {ticket, url}
    WEB->>API: WSS /ws/v1
    API-->>WEB: open
    WEB->>API: {"type":"auth","ticket":"..."}
    API->>RD: GETDEL ws:ticket:{t}
    API-->>WEB: {"type":"auth.ok","connection_id":...,"heartbeat_s":15}
    WEB->>API: {"type":"subscribe","channels":["fleet","twin:UUID"]}
    API->>RD: SUBSCRIBE evt.fleet, evt.twin.UUID
    API-->>WEB: {"type":"subscribed","channels":[...],"snapshot":{...}}
    loop
        API-->>WEB: {"type":"twin.delta",...}
        WEB->>API: {"type":"ping","seq":n}
        API-->>WEB: {"type":"pong","seq":n}
    end
    Note over WEB,API: network drop
    WEB->>WEB: exponential backoff 0.5→8s + jitter
    WEB->>API: POST /ws/ticket (new)
    WEB->>API: reconnect + {"type":"resume","last_seq":{...}}
    API-->>WEB: {"type":"resync","snapshot":{...}} (full state, no gap replay)
```

---

## 11.7 SEQ-07 — Fleet dashboard load & live sort

```mermaid
sequenceDiagram
    autonumber
    participant WEB
    participant API
    participant RD
    participant PG

    WEB->>API: GET /fleet?sort=priority&order=desc&page=1&size=50
    API->>RD: ZREVRANGE fleet:rank:priority 0 49
    RD-->>API: 50 engine ids
    API->>RD: pipeline HGETALL twin:{id}:state ×50
    alt cache miss
        API->>PG: SELECT latest snapshots for missing ids
    end
    API-->>WEB: 200 {items, total, aggregates}
    WEB->>API: WS subscribe "fleet"
    loop 1 Hz
        API-->>WEB: {"type":"fleet.delta","changed":[{id,hi,band,rul,priority}]}
        WEB->>WEB: patch Zustand map; re-sort only if sortKey affected; virtualized rows update
    end
```

---

## 11.8 SEQ-08 — Model inference with circuit breaker

```mermaid
sequenceDiagram
    autonumber
    participant TE as twin-engine
    participant CB as CircuitBreaker
    participant INF as inference

    TE->>CB: call(predict_batch)
    CB->>INF: POST /predict/batch (timeout 50ms)
    INF--xCB: 500 / timeout
    CB->>CB: failures 1..5
    CB->>CB: OPEN for 30s
    TE->>TE: use last-good prediction, set prediction_stale=true
    TE->>RD: PUBLISH evt.twin.{id} {prediction_stale:true}
    Note over TE: UI shows "prediction stale (12s)" chip
    CB->>INF: after 30s → HALF_OPEN, 1 probe
    INF-->>CB: 200
    CB->>CB: CLOSED
    TE->>RD: PUBLISH {prediction_stale:false}
```

---

## 11.9 SEQ-09 — RAG retrieval inside the knowledge agent

```mermaid
sequenceDiagram
    autonumber
    participant AG as knowledge_agent
    participant MCP as knowledge_server
    participant PG as Postgres FTS
    participant CH as ChromaDB
    participant RR as Reranker

    AG->>MCP: rag_search{query, filters:{module:"HPC", source_type:["AMM","FAA"]}, k:20}
    par hybrid
        MCP->>PG: ts_rank_cd BM25 top-20
        and
        MCP->>CH: dense similarity top-20 (metadata filtered)
    end
    MCP->>MCP: Reciprocal Rank Fusion
    MCP->>RR: cross-encode (query, chunk) ×20
    RR-->>MCP: scores → top-5
    MCP->>PG: fetch authoritative chunk text + document metadata
    MCP-->>AG: [{chunk_id, doc_title, section_path, page, quote, score}]
    AG->>AG: build Evidence[] with citation refs [1]..[5]
```

---

## 11.10 SEQ-10 — Maintenance approval (human-in-the-loop)

```mermaid
sequenceDiagram
    autonumber
    actor P as Planner
    participant WEB
    participant API
    participant PG
    participant RD
    participant TE as twin-engine

    P->>WEB: open WorkPackageBoard → review DRAFT WP-1042 (rationale + citations + cost)
    P->>WEB: Approve, schedule slot 2026-08-03
    WEB->>API: POST /maintenance/work-packages/{id}/approve {slot}
    API->>API: RBAC require role=planner; status transition guard DRAFT→APPROVED
    API->>PG: UPDATE work_packages; INSERT audit row
    API->>RD: XADD cmd.twin {type:PERFORM_MAINTENANCE, module:HPC, effectiveness:0.6, at_cycle:next}
    API-->>WEB: 200 {status:APPROVED}
    TE->>TE: at next tick apply maintenance effect → component restored, ceiling released
    TE->>PG: INSERT maintenance_events (health_before 34.1 → after 71.8)
    TE->>RD: PUBLISH evt.twin.{id} {maintenance.performed}
    RD-->>API-->>WEB: WS → 3D exploded view animation + health bar rises + timeline entry
```

---

## 11.11 SEQ-11 — Deep explanation on demand (XAI)

```mermaid
sequenceDiagram
    autonumber
    actor U
    participant WEB
    participant API
    participant INF
    participant RD

    U->>WEB: XaiPanel → "Explain deeply" (KernelSHAP)
    WEB->>API: POST /engines/{id}/explain {cycle, method:"shap"}
    API->>RD: GET explain-cache {engine,cycle,method}
    alt hit
        RD-->>API: cached
    else miss
        API->>INF: POST /explain {window, method:"shap", nsamples:200}
        INF->>INF: KernelSHAP (~1.5s)
        INF-->>API: shapley values (W×F)
        API->>RD: SETEX cache 1h
    end
    API-->>WEB: {top_sensors[], temporal_saliency[][], module_attribution{}, narrative}
    WEB->>WEB: bar chart + heatstrip; 3D highlights the attributed module
```

---

## 11.12 SEQ-12 — Crash recovery of twin-engine

```mermaid
sequenceDiagram
    autonumber
    participant TE1 as twin-engine (killed)
    participant RD as Redis
    participant TE2 as twin-engine (restarted)
    participant PG as Postgres
    participant API

    Note over TE1: SIGKILL mid-tick
    RD->>RD: lock:shard:0 expires after ≤15s
    TE2->>RD: SET lock:shard:0 NX PX 15000 → acquired
    TE2->>PG: latest snapshot per unit + events after snapshot.seq
    TE2->>TE2: deterministic replay → identical TwinState
    TE2->>RD: XAUTOCLAIM cmd.twin pending entries (>30s idle)
    TE2->>TE2: resume clock from persisted cycle (not wall time)
    TE2->>RD: PUBLISH evt.fleet {engine_restored:true}
    API-->>API: clients see a brief "reconnecting" pill, then continuity
    Note over TE2,PG: chaos test asserts state hash equality pre/post kill
```
