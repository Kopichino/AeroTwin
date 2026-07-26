# 12 — API Documentation

Base: `/api/v1` · JSON only · UTC ISO-8601 · UUIDv7 ids · Auth: `Authorization: Bearer <jwt>`
Versioning: URI-versioned; additive changes are non-breaking; removals require `/v2`.
Every response carries `X-Trace-Id`. Errors are RFC 9457 Problem Details.

---

## 12.1 Conventions

| Concern | Rule |
|---|---|
| Pagination | `?page=1&size=50` (max 200) → `{items, page, size, total, has_next}`; keyset (`?cursor=`) for time-series and timelines |
| Sorting | `?sort=field&order=asc\|desc`; whitelist per endpoint |
| Filtering | explicit named params, never generic query DSL |
| Partial fields | `?fields=id,health_index,rul_p50` |
| Idempotency | `Idempotency-Key` header honored on all POSTs that create resources |
| Rate limit headers | `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` |
| Caching | `ETag` + `If-None-Match` on documents and fleet summary |
| Async ops | `202 Accepted` + resource id + WS channel to watch |
| Engine ref | Accepts UUID **or** external ref `FD001-train-U27` everywhere |

---

## 12.2 Auth

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/auth/login` | `{email, password}` | `{access_token, refresh_token, expires_in, user}` |
| POST | `/auth/refresh` | `{refresh_token}` | new pair |
| POST | `/auth/logout` | — | 204 |
| GET | `/auth/me` | — | `{id,email,display_name,role,permissions[]}` |
| POST | `/ws/ticket` | — | `{ticket, url, expires_in:30}` |

Roles: `viewer < engineer < planner < admin`.

---

## 12.3 Fleet

**GET `/fleet`** — paginated fleet listing.
Query: `sort=priority|health|rul|risk|unit|cycle`, `order`, `band=HEALTHY,WARNING`, `subset=FD001`,
`status=RUNNING`, `min_health`, `max_rul`, `has_anomaly=true`, `search=`, `page`, `size`.
```json
{
  "items": [{
    "engine_id": "018f...", "external_ref": "FD001-train-U27", "tail_number": "AT-0027",
    "subset": "FD001", "status": "RUNNING", "cycle": 178,
    "health_index": 34.1, "health_band": "CRITICAL", "degradation_rate": -0.42,
    "rul_p50": 19.4, "rul_p10": 11.2, "rul_p90": 31.8, "prediction_stale": false,
    "failure_prob": {"30": 0.71, "60": 0.94, "90": 0.99},
    "anomaly_score": 3.4, "open_anomalies": 2,
    "worst_module": "HPC", "component_health": {"FAN": 81.2, "HPC": 41.0, "HPT": 55.7},
    "priority_score": 0.87, "risk_tier": "R1",
    "updated_at": "2026-07-26T09:14:22Z"
  }],
  "page":1,"size":50,"total":260,"has_next":true,
  "aggregates":{"by_band":{"HEALTHY":142,"WATCH":61,"WARNING":38,"CRITICAL":19},
                "avg_health":68.4,"engines_at_risk":57,"predicted_failures_30d":12}
}
```

**GET `/fleet/summary`** → KPI tiles (cached 2 s, ETag).
**GET `/fleet/risk-matrix`** → 2-D buckets `(probability × consequence)` with engine ids.
**GET `/fleet/rankings?criterion=priority&limit=10`** → top-N.
**POST `/fleet/compare`** `{engine_ids[], metrics[]}` → aligned series for overlay charts.

---

## 12.4 Engines & twin

| Method | Path | Purpose |
|---|---|---|
| GET | `/engines/{ref}` | Full twin detail (state, components, prediction, anomalies, last events) |
| GET | `/engines/{ref}/state` | Lightweight current state |
| GET | `/engines/{ref}/telemetry?from_cycle&to_cycle&sensors=s2,s3&max_points=500` | Downsampled series with baseline + z-score |
| GET | `/engines/{ref}/history?span=1h\|6h\|all&resolution=raw\|1m` | Chart-ready series (uses continuous aggregate) |
| GET | `/engines/{ref}/timeline?cursor&limit=50&types=` | Event timeline (keyset) |
| GET | `/engines/{ref}/components` | Component health + drivers + degradation rates |
| GET | `/engines/{ref}/components/{module}` | Module drill-down incl. related sensors & AMM tasks |
| GET | `/engines/{ref}/predictions?from_cycle&to_cycle&model_id=` | Prediction history (for the RUL timeline chart) |
| GET | `/engines/{ref}/predictions/latest` | Current prediction + attributions |
| GET | `/engines/{ref}/anomalies?resolved=false` | Anomaly list |
| POST | `/engines/{ref}/anomalies/{id}/acknowledge` | Ack (engineer+) |
| POST | `/engines/{ref}/explain` | `{cycle, method: ig\|shap\|attention}` → attributions (11.11) |
| GET | `/engines/{ref}/snapshots?cycle=` | Historical snapshot at a cycle |

**GET `/engines/{ref}` response (abridged):**
```json
{
  "engine": {"id":"018f...","external_ref":"FD001-train-U27","subset":"FD001",
             "engine_model":"AT-9000","total_cycles":206,"tail_number":"AT-0027"},
  "twin": {"status":"RUNNING","cycle":178,"regime":0,
           "health_index":34.1,"health_band":"CRITICAL","degradation_rate":-0.42,
           "time_in_band":{"HEALTHY":96,"WATCH":41,"WARNING":29,"CRITICAL":12}},
  "sensors": {"s2":642.5,"s3":1591.2,"s4":1408.9,"...":0},
  "sensor_meta": {"s3":{"name":"T30","unit":"degR","baseline":1583.1,"z":3.2,"module":"HPC"}},
  "components": [{"module":"HPC","score":41.0,"degradation_rate":-0.51,
                  "drivers":["T30 +3.2σ","Ps30 -2.8σ","htBleed +2.1σ"],
                  "last_maintained_cycle":null}],
  "prediction": {"model_id":"018e...","model_name":"transformer-rul","version":"v1.3.0",
                 "rul_p50":19.4,"rul_p10":11.2,"rul_p90":31.8,
                 "failure_prob":{"30":0.71,"60":0.94,"90":0.99},
                 "attributions":[{"sensor":"s11","name":"Ps30","value":0.19,"direction":"down","module":"HPC"}],
                 "computed_at_cycle":178,"stale":false},
  "anomalies": [{"id":"018f...","cycle":171,"detector":"cusum","score":3.4,
                 "severity":"HIGH","module":"HPC","sensors":[{"sensor":"s20","z":-3.1}]}],
  "recent_events": [{"cycle":176,"type":"twin.health.band_changed","payload":{"from":"WARNING","to":"CRITICAL"}}],
  "open_work_packages": [{"id":"018f...","title":"HPC borescope + performance restoration","status":"DRAFT"}]
}
```

---

## 12.5 Replay control (engineer+)

| Method | Path | Body |
|---|---|---|
| POST | `/replay/start` | `{engine_refs?: [], all?: true}` |
| POST | `/replay/pause` / `/replay/resume` | same |
| POST | `/replay/speed` | `{speed: 0.5\|1\|2\|4\|8\|16\|32, engine_refs?}` |
| POST | `/engines/{ref}/replay/seek` | `{cycle}` |
| POST | `/engines/{ref}/replay/reset` | — |
| POST | `/engines/{ref}/fault-injection` | `{sensor, bias, duration_cycles}` (admin) |
| POST | `/engines/{ref}/maintenance` | `{module, action, effectiveness?}` (planner+) |
| GET | `/replay/status` | `{speed, running, active_twins, lag_ms, shard_health[]}` |

All return `202` — commands are asynchronous (single-writer rule).

---

## 12.6 Simulation

**POST `/engines/{ref}/simulate`** → `202 {simulation_id, channel:"sim:{id}"}`
```json
{ "base_cycle": 178, "horizon_cycles": 100, "monte_carlo_paths": 50, "seed": 42,
  "scenario": { "name": "Delay maintenance 40 cycles under hot conditions",
    "interventions": [
      {"type":"sensor_bias","target":"s3","magnitude":8.0,"unit":"degR","start_cycle":0},
      {"type":"regime_shift","to_regime":5,"start_cycle":10},
      {"type":"maintenance_delay","cycles":40},
      {"type":"maintenance_action","module":"HPC","effectiveness":0.7,"at_cycle":60}
    ]}}
```
**GET `/simulations/{id}`**
```json
{"id":"...","status":"DONE",
 "baseline":{"rul_curve":[{"cycle":178,"p50":19.4,"p10":11.2,"p90":31.8}],
             "failure_cycle":{"p10":189,"p50":197,"p90":210},
             "health_curve":[...]},
 "scenario":{"rul_curve":[...],"failure_cycle":{"p10":182,"p50":188,"p90":196},"health_curve":[...]},
 "delta":{"rul_change_p50":-9.0,"failure_prob_30_change":0.18,
          "first_critical_cycle_change":-11,"cost_delta_usd":142000,
          "verdict":"NOT_RECOMMENDED",
          "narrative":"Delaying maintenance by 40 cycles advances predicted failure by ~9 cycles and raises 30-cycle failure probability from 0.71 to 0.89."}}
```
**GET `/engines/{ref}/simulations`** · **POST `/simulations/{id}/cancel`** ·
**GET `/simulations/templates`** (prebuilt scenarios for the demo).

---

## 12.7 Copilot & agents

| Method | Path | Purpose |
|---|---|---|
| POST | `/copilot/ask` | `{conversation_id?, engine_ref?, message, stream:true}` → `202 {run_id, conversation_id, channel}` |
| GET | `/copilot/conversations` | list |
| GET | `/copilot/conversations/{id}` | messages + citations |
| DELETE | `/copilot/conversations/{id}` | — |
| GET | `/copilot/suggestions?engine_ref=` | Context-aware prompt chips |
| GET | `/agents/runs/{run_id}` | Run detail: status, answer, confidence, tokens, duration |
| GET | `/agents/runs/{run_id}/trace` | **Full trace**: steps, nodes, prompts (version only), tool calls, timings |
| GET | `/agents/runs?engine_ref=&intent=&limit=` | Run history |
| GET | `/agents/graph` | Graph topology JSON → renders `AgentGraphViz` |
| GET | `/agents/health` | Provider status, degraded mode flag, cache hit rate |

**Trace response shape** (drives the tool-trace viewer — a headline demo feature):
```json
{"run_id":"...","intent":"DIAGNOSIS","graph_version":"v1.2","status":"COMPLETED",
 "duration_ms":6420,"tokens":{"in":4210,"out":812},"degraded":false,
 "steps":[{"seq":1,"node":"router","agent":null,"duration_ms":310,
           "output":{"intent":"DIAGNOSIS","entities":{"engine":"FD001-train-U27"}}},
          {"seq":2,"node":"diagnosis_agent","duration_ms":2980,"prompt_version":"diagnosis@v3",
           "tool_calls":[{"server":"twin","tool":"get_component_health","duration_ms":41,"ok":true}]}],
 "citations":[{"n":1,"document":"AT-9000 AMM","section":"72-31-00","page":412,
               "quote":"Perform borescope inspection of HPC stages 3-9 when...","chunk_id":"..."}]}
```

---

## 12.8 Knowledge, maintenance, models, admin

**Knowledge**
`GET /knowledge/search?q=&module=&source_type=&k=10` → chunks + highlights + scores ·
`GET /knowledge/documents` · `GET /knowledge/documents/{id}` · `GET /knowledge/chunks/{id}` ·
`GET /knowledge/stats` · `POST /knowledge/reindex` (admin).

**Maintenance**
`GET /maintenance/work-packages?status=&engine_ref=&severity=` ·
`GET /maintenance/work-packages/{id}` ·
`POST /maintenance/work-packages` (manual create, planner+) ·
`POST /maintenance/work-packages/{id}/approve` `{scheduled_slot}` ·
`POST /maintenance/work-packages/{id}/reject` `{reason}` ·
`GET /maintenance/slots?from=&to=` · `GET /maintenance/schedule` (Gantt data) ·
`GET /maintenance/cost-summary?horizon_days=30`.

**Models**
`GET /models` · `GET /models/{id}` (metrics, hyperparams, card) ·
`GET /models/comparison?subset=FD001` (the model-comparison table for the report page) ·
`POST /models/{id}/promote` `{stage}` (admin, metric-gated) ·
`GET /models/{id}/card` (markdown).

**Admin/system**
`GET /health/live` · `/health/ready` · `/health/deep` · `GET /metrics` (Prometheus) ·
`GET /admin/system` (shards, lag, queue depths, WS connections, cache hit rates) ·
`GET /admin/events/tail?limit=100` · `POST /admin/seed` (dev only) ·
`POST /telemetry/client-error` (frontend error sink).

---

## 12.9 Error codes

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `VALIDATION_FAILED` | Body/query failed schema validation (`errors[]` populated) |
| 400 | `INVALID_SCENARIO` | Simulation scenario semantically invalid |
| 401 | `UNAUTHENTICATED` | Missing/expired token |
| 403 | `FORBIDDEN_ROLE` | Role insufficient |
| 404 | `ENGINE_NOT_FOUND` / `RUN_NOT_FOUND` / `DOCUMENT_NOT_FOUND` | — |
| 409 | `TWIN_INVALID_TRANSITION` | FSM rejects the command |
| 409 | `WORK_PACKAGE_STATE_CONFLICT` | Illegal status transition |
| 422 | `CYCLE_OUT_OF_RANGE` | Seek/explain beyond trajectory |
| 429 | `RATE_LIMITED` | Bucket exhausted (`Retry-After`) |
| 503 | `INFERENCE_UNAVAILABLE` | Circuit breaker open (response includes last-good, `stale:true`) |
| 503 | `LLM_UNAVAILABLE` | Provider down → degraded copilot (still 200 with `degraded:true` where possible) |
| 504 | `AGENT_TIMEOUT` | Run exceeded wall budget; partial answer returned |
| 500 | `INTERNAL` | Unexpected; `trace_id` for support |

---

## 12.10 OpenAPI & client generation
FastAPI emits OpenAPI 3.1 at `/openapi.json`; CI exports to `docs/api/openapi.json`, runs
`openapi-typescript` + `orval` into `packages/api-client`, and **fails the build on any uncommitted
diff** (P6/NFR-12). Schemathesis fuzzes every endpoint against the schema in the integration job.
