# M7 — Engine Detail & Charts — Completion Report

**Status:** complete, awaiting approval
**Commit:** `9fb64f4` · 453 backend + 29 frontend tests · 11 architecture contracts kept

---

## (a) What was built

### Backend

| Component | Detail |
|---|---|
| **`at_twin.history`** | Bounded ring buffer per engine (600 samples). Decimated **on read**, not on write — writes happen every cycle and must stay cheap. Cleared on recycle. |
| **`GET /engines/{ref}/history`** | Health, RUL with p10/p90 band, anomaly score, 12 charted sensors, 7 component scores. `limit` and `from_cycle` params. |
| **`GET /engines/{ref}/explain`** | On-demand gradient attribution. Returns a structured *reason* when unavailable rather than an error. |
| **`/system`** | Now reports history usage (engines, total samples). |

### Frontend

| Component | Detail |
|---|---|
| **`components/charts`** | SVG line chart with confidence band, sparklines, attribution bars — each with an accessible `<table>` fallback |
| **`features/engine`** | Detail view: header, **persistent canvas slot**, four tabs (Overview / Sensors / Prediction & XAI / Components) |
| **`app/engines/[id]`** | Dynamic route; fleet rows navigate to it |

---

## (b) Evidence — verified against the live stack

| check | result |
|---|---|
| History depth | **200 samples** spanning cycle 65 → 281 |
| Degradation captured | HI **94.4 → 16.8**, RUL **125 → 1.7** |
| Conformal band present | ✅ p10/p90 on every sample |
| Attribution | **HPC dominant** (0.175) on a degraded engine, ahead of COMBUSTOR (0.062) |
| Tick p99 under load | **107.8 ms** (budget 120 ms) |
| History memory | 100 engines, 7,934 samples |
| Frontend build | 4 routes incl. dynamic `/engines/[id]` |
| Tests | 453 backend, 29 frontend, mypy clean (59 files) |

`docs/preview/engine-detail-preview.html` renders the real view from captured live data.

---

## (c) Two decisions worth flagging

**1. No charting library.** These are three specific chart shapes over a few hundred points. Recharts or visx would add ~150 KB to draw polylines expressible in a dozen lines of SVG — and plain SVG stays inspectable and styleable by the design tokens. If M8+ needs brushing or zoom, that calculus changes and I'll revisit it.

**2. History is REST, not WebSocket.** It's a bounded read that changes slowly. Pushing 200 samples down the socket every tick would dwarf the deltas they accompany. The detail view polls every 3 s while live state continues to stream.

---

## (d) One test problem worth recording

Three API tests failed initially because they queried history *before* the twin runner's first tick — the runner is an asyncio task started by the app lifespan, so a freshly created `TestClient` has no data yet.

I replaced them with a **polling precondition** (`wait_for_history`) rather than a fixed `sleep`. A sleep would be either flaky on a slow runner or needlessly slow on a fast one; polling for the actual precondition is deterministic and fast in both cases.

---

## (e) Honest gaps

- **The canvas slot is a placeholder.** It currently shows the component health readout. That's deliberate — the slot exists so M8 is a component swap, not a page rewrite — but the 3D engine is not here yet.
- **No brushing, zoom, or crosshair.** Charts are read-only. Doc 15 §15.2.3 specifies brush-zoom on the sensor tab.
- **History is in-memory and unbounded across restarts.** It vanishes on restart, and Postgres (Doc 04) remains unwired. Fine for a demo; not durable.
- **Attribution shows a duplicate sensor.** `Ps30` appears twice in the top-8 because `s11` and its engineered `s11_m5` variant both map to the same base sensor. Cosmetic, but it looks like a bug and should be deduplicated.
- **Still no virtualization** on the fleet grid (carried from M6).

---

## (f) Cumulative state

| Metric | M4 | M5 | M6 | M7 |
|---|---|---|---|---|
| Backend tests | 372 | 423 | 423 | **453** |
| Frontend tests | — | — | 29 | 29 |
| Contracts | 11 | 11 | 11 | 11 |
| API routes | 7 | 7 | 8 | **10** |

---

## (g) Next: M8 — 3D Digital Twin Visualisation

The showpiece. React Three Fiber turbofan in the canvas slot: modules coloured by health, fan rotating at `Nf`, hotspots on anomalous modules, click-to-inspect, exploded and X-ray views, with a 2D SVG fallback when WebGL is unavailable.

One risk to flag up front: `three` + `@react-three/fiber` + `drei` is a substantial dependency addition, and this sandbox has already hit its disk ceiling once. I'll install only what's needed and check the budget before committing to `drei`.
