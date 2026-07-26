# 10 — Data Flow Diagrams

## 10.1 Level 0 — Context DFD

```mermaid
flowchart LR
    E1["External Entity:<br/>Reliability Engineer"]
    E2["External Entity:<br/>C-MAPSS Dataset Files"]
    E3["External Entity:<br/>Knowledge Corpus<br/>(NASA/FAA/AMM)"]
    E4["External Entity:<br/>LLM Provider"]

    P0(("0<br/>AeroTwin<br/>Platform"))

    E2 -->|"raw telemetry rows"| P0
    E3 -->|"documents"| P0
    E1 -->|"commands, queries, approvals"| P0
    P0 -->|"live twin state, RUL, alerts,<br/>3D health, answers, work packages"| E1
    P0 <-->|"prompts / completions"| E4
```

---

## 10.2 Level 1 — Major processes

```mermaid
flowchart TB
    E2[(C-MAPSS files)]
    E3[(Knowledge corpus)]
    E1([Engineer])
    E4([LLM])

    P1(("1.0<br/>Ingest &<br/>Provision"))
    P2(("2.0<br/>Replay &<br/>Twin Update"))
    P3(("3.0<br/>Predict &<br/>Detect"))
    P4(("4.0<br/>Publish &<br/>Fan-out"))
    P5(("5.0<br/>Agentic<br/>Reasoning"))
    P6(("6.0<br/>Simulate"))
    P7(("7.0<br/>Present<br/>(Web/3D)"))
    P8(("8.0<br/>Knowledge<br/>Indexing"))

    D1[("D1 engines")]
    D2[("D2 telemetry")]
    D3[("D3 twin_events + snapshots")]
    D4[("D4 predictions + anomalies")]
    D5[("D5 models registry")]
    D6[("D6 doc_chunks + vectors")]
    D7[("D7 agent_runs/steps/citations")]
    D8[("D8 work_packages")]
    D9[("D9 Redis hot state + bus")]

    E2 --> P1 --> D1
    P1 --> D2
    D1 --> P2
    D2 --> P2
    P2 --> D3
    P2 --> D9
    P2 --> P3
    D5 --> P3
    P3 --> D4
    P3 --> P2
    P2 --> P4
    P3 --> P4
    D9 --> P4
    P4 --> P7
    P7 --> E1
    E1 --> P7
    P7 --> P2
    P7 --> P6
    P6 --> P2
    P6 --> P7
    E1 --> P5
    P5 <--> E4
    P5 --> D7
    P5 --> D8
    D3 --> P5
    D4 --> P5
    D6 --> P5
    E3 --> P8 --> D6
    P5 --> P7
```

**Process definitions**

| ID | Process | Input | Output | Store touched |
|---|---|---|---|---|
| 1.0 | Ingest & Provision | raw txt | engine rows, parquet, seeded fleet | D1, D2 |
| 2.0 | Replay & Twin Update | telemetry row, commands | twin state, events, deltas | D2, D3, D9 |
| 3.0 | Predict & Detect | window tensor | RUL, intervals, attributions, anomalies | D4, D5 |
| 4.0 | Publish & Fan-out | deltas | WS frames per channel | D9 |
| 5.0 | Agentic Reasoning | query/trigger + evidence | answer, citations, work package | D3,D4,D6,D7,D8 |
| 6.0 | Simulate | scenario spec | trajectories, deltas | D3 (read), simulations |
| 7.0 | Present | WS frames + REST | rendered UI, 3D | — |
| 8.0 | Knowledge Indexing | documents | chunks + embeddings | D6 |

---

## 10.3 Level 2 — Process 2.0 (Replay & Twin Update)

```mermaid
flowchart LR
    D2[(telemetry source)]
    C[(cmd.twin stream)]
    P21(("2.1 Advance<br/>replay clock"))
    P22(("2.2 Read next<br/>telemetry row"))
    P23(("2.3 Detect<br/>regime"))
    P24(("2.4 Compute<br/>residuals vs baseline"))
    P25(("2.5 Physics<br/>proxies → component health"))
    P26(("2.6 Fuse<br/>Health Index"))
    P27(("2.7 Apply<br/>hysteresis + emit events"))
    P28(("2.8 Batch<br/>persist"))
    P29(("2.9 Coalesce<br/>+ publish delta"))
    P2A(("2.10 Handle<br/>command (FSM)"))

    D2 --> P22
    P21 --> P22 --> P23 --> P24 --> P25 --> P26 --> P27
    P27 --> P28
    P27 --> P29
    C --> P2A --> P27
    P24 -->|residuals| ANOM(("3.2 anomaly"))
    P22 -->|window| INF(("3.1 inference"))
    INF -->|rul, attributions| P26
    ANOM -->|anomaly score| P26
```

---

## 10.4 Level 2 — Process 3.0 (Predict & Detect)

```mermaid
flowchart TB
    W["window buffer<br/>(B, W, F)"] --> S["3.1.1 apply per-regime scaler"]
    S --> B["3.1.2 micro-batch"]
    B --> M["3.1.3 forward pass<br/>(TorchScript, production model)"]
    M --> U["3.1.4 uncertainty<br/>MC-dropout / ensemble → p10/p50/p90"]
    U --> FP["3.1.5 failure probability<br/>P(RUL<30/60/90)"]
    M --> X{"explain?<br/>every 10 cycles"}
    X -->|yes| IG["3.1.6 Integrated Gradients<br/>→ top-k sensors + saliency"]
    X -->|no| SKIP[ ]
    IG --> ATTR["3.1.7 map sensors → modules<br/>(attribution matrix)"]

    R["residual stream"] --> E1["3.2.1 EWMA z-score"]
    R --> E2["3.2.2 CUSUM change point"]
    R --> E3["3.2.3 Autoencoder recon error"]
    E1 & E2 & E3 --> F["3.2.4 fuse → score, severity,<br/>contributing sensors"]
    F --> ATTR

    FP & ATTR --> OUT[("predictions / anomalies")]
```

---

## 10.5 Level 2 — Process 5.0 (Agentic Reasoning)

```mermaid
flowchart TB
    Q["user query / auto trigger"] --> G1["5.1 input guard"]
    G1 --> R["5.2 router: intent + entities"]
    R --> A["5.3 specialist agents<br/>(health/diag/plan/sim/fleet/knowledge)"]
    A --> T["5.4 MCP tool invocation"]
    T --> TW[("twin tools → D3/D9")]
    T --> KN[("knowledge tools → D6")]
    T --> MT[("maintenance tools → D8")]
    TW & KN & MT --> EV["5.5 evidence accumulation"]
    EV --> SY["5.6 synthesizer (LLM)"]
    SY --> CR{"5.7 critic:<br/>grounded? cited? complete?"}
    CR -->|fail < 2| SY
    CR -->|need evidence| R
    CR -->|pass| G2["5.8 output guard<br/>citation + numeric check"]
    G2 --> P["5.9 persist run/steps/tool calls/citations"]
    G2 --> STR["5.10 stream tokens → WS"]
```

---

## 10.6 Data dictionary (selected flows)

| Flow | Schema | Rate |
|---|---|---|
| `telemetry_row` | `{unit, cycle, op[3], s[21]}` | speed × 1/s per engine |
| `twin_delta` | `{engine_id, cycle, hi, band, components{}, rul{}, anomaly, sensors_subset{}}` | ≤ 4 Hz per engine (coalesced) |
| `fleet_delta` | `{counts_by_band, avg_hi, at_risk[], changed[]}` | 1 Hz |
| `inference_request` | `{model_id, windows: f32[B][W][F], mask, explain}` | 1/tick batched |
| `inference_response` | `{rul_p10/p50/p90[], probs[], attributions[]?, latency_ms}` | 1/tick |
| `agent_token` | `{run_id, delta, index}` | ~30–80/s during synthesis |
| `evidence_item` | `{source_type, ref, content, confidence, tool_call_id}` | per tool call |
