# 15 — UI Page Hierarchy & Design Specification

## 15.1 Route tree (Next.js App Router)

```
app/
├── layout.tsx                        # providers, fonts, theme
├── page.tsx                          # "/"  landing: hero 3D engine, value props, CTA → /fleet
├── login/page.tsx
├── (app)/                            # authenticated route group — AppFrame shell
│   ├── layout.tsx                    # Sidebar + TopBar + WS provider + CommandPalette
│   ├── overview/page.tsx             # "/overview"  Mission Control
│   ├── fleet/
│   │   ├── page.tsx                  # "/fleet"  grid/table toggle
│   │   └── @drawer/(.)engines/[id]/  # intercepted route → quick-look drawer (no navigation)
│   ├── engines/
│   │   └── [id]/
│   │       ├── layout.tsx            # engine header + 3D canvas persist across tabs
│   │       ├── page.tsx              # tab: Overview
│   │       ├── sensors/page.tsx      # tab: Sensors
│   │       ├── prediction/page.tsx   # tab: Prediction & XAI
│   │       ├── components/page.tsx   # tab: Component Health
│   │       ├── anomalies/page.tsx    # tab: Anomalies
│   │       ├── timeline/page.tsx     # tab: Timeline
│   │       ├── simulate/page.tsx     # tab: Simulation
│   │       └── copilot/page.tsx      # tab: Ask about this engine
│   ├── copilot/page.tsx              # "/copilot"  full-screen assistant
│   ├── maintenance/
│   │   ├── page.tsx                  # work package board (kanban)
│   │   └── schedule/page.tsx         # Gantt of slots
│   ├── knowledge/
│   │   ├── page.tsx                  # search
│   │   └── documents/[id]/page.tsx   # doc viewer w/ highlight
│   ├── analytics/page.tsx            # model comparison, fleet trends, drift
│   └── admin/
│       ├── page.tsx                  # system health
│       ├── replay/page.tsx           # replay console
│       ├── models/page.tsx           # registry + promotion
│       └── agents/page.tsx           # run history + trace explorer
├── loading.tsx / error.tsx / not-found.tsx        (+ per-route variants)
└── api/                              # BFF-lite: /api/ws-proxy, /api/health (edge)
```

The **intercepted route** on the fleet page (`@drawer/(.)engines/[id]`) gives a quick-look side panel
without losing scroll position — a Linear-style touch that reads as senior product thinking.

---

## 15.2 Page specifications

### 15.2.1 `/overview` — Mission Control (the money screenshot)
```
┌──────────────────────────────────────────────────────────────────────────┐
│ KPI strip: Active Engines · Avg Health · At Risk · Predicted Failures 30d│
│            Open Work Packages · Fleet Availability %                     │
├───────────────────────────────┬──────────────────────────────────────────┤
│ Fleet health distribution     │ Live 3D hero: worst engine, auto-rotate  │
│ (stacked area, last 200 cyc)  │ + health ring + module hotspots          │
├───────────────────────────────┼──────────────────────────────────────────┤
│ Top-10 priority engines       │ Agent activity feed (live)               │
│ (sortable mini-table)         │ "Diagnosis agent flagged HPC on U27…"    │
├───────────────────────────────┴──────────────────────────────────────────┤
│ Risk matrix (prob × consequence heat grid, click → filtered fleet)       │
└──────────────────────────────────────────────────────────────────────────┘
```

### 15.2.2 `/fleet`
- Sticky `FilterBar`: search, subset, band multi-select, status, anomaly toggle, sort dropdown, grid/table switch, density.
- **Grid mode:** virtualized cards. Card = tail id, sparkline of HI (last 60 cycles), HI ring gauge,
  band pill, RUL with p10–p90 whisker, worst module chip, anomaly badge, priority rank. Hover →
  prefetch + LOD1 engine preview. Click → quick-look drawer; ⌘-click → full page.
- **Table mode:** TanStack Table, column visibility, multi-sort, CSV export, row selection → bulk compare.
- Empty/loading/error states designed, not default.

### 15.2.3 `/engines/[id]` — persistent shell
```
┌───────────────────────────────────────────────────────────────────────────┐
│ Header: AT-0027 · FD001-U27 · [CRITICAL] · cycle 178/206 · ⏸ ⏵ speed 8×  │
│         HI 34.1 ▼0.42/cyc · RUL 19 (11–32) · model transformer-rul v1.3.0│
├──────────────────────────────┬────────────────────────────────────────────┤
│                              │  Right rail (context-sensitive):           │
│   3D CANVAS (sticky)         │   • Component health tree                  │
│   turbofan, module colors,   │   • Live sensor grid (12 tiles, sparkline) │
│   fan spinning at Nf,        │   • Open anomalies                         │
│   hotspots, exploded/X-ray   │   • Agent insights (auto-generated cards)  │
│   toolbar: presets E X R     │                                            │
├──────────────────────────────┴────────────────────────────────────────────┤
│ Tabs: Overview · Sensors · Prediction & XAI · Components · Anomalies ·    │
│       Timeline · Simulation · Copilot                                     │
│ ── tab content ──                                                         │
└───────────────────────────────────────────────────────────────────────────┘
```
The canvas lives in the **layout**, so switching tabs never remounts WebGL (major UX + perf win).

**Tab contents**
- *Overview:* health trend, RUL trend with confidence band, top drivers, recent events, quick actions.
- *Sensors:* 21 sensor cards with live value, baseline, z-score, module tag; multi-select → overlay chart; brush-zoom; normalize toggle; anomaly shading.
- *Prediction & XAI:* RUL timeline (actual vs predicted, uncertainty band, historical prediction accuracy), attribution bar chart, temporal saliency heatstrip, plain-language explanation, "Explain deeply (SHAP)" button, model selector to compare architectures on this engine.
- *Components:* module tree with scores, degradation rates, drivers, last maintenance; clicking a module syncs the 3D camera; linked AMM task cards.
- *Anomalies:* timeline of detections, detector attribution, contributing sensors, ack flow.
- *Timeline:* unified vertical event stream (health, prediction, anomaly, agent, maintenance) with filters and a scrubber that **replays past state into the 3D view**.
- *Simulation:* scenario builder (left) + comparison charts (right) + delta verdict card + template library.
- *Copilot:* engine-scoped chat.

### 15.2.4 `/copilot`
Three columns: conversation list · chat (streaming markdown, citation chips, suggested prompts,
inline mini-charts rendered from artifacts) · inspector (live agent graph with the active node
pulsing, step timings, tool-call table, evidence list, token/cost meter, "degraded mode" badge).
The inspector is what proves this is a *multi-agent system* and not a wrapper around one prompt.

### 15.2.5 `/maintenance`
Kanban: DRAFT → PROPOSED → APPROVED → SCHEDULED → IN PROGRESS → COMPLETED. Card shows engine,
severity, cost, downtime, due-by cycle, "authored by Maintenance Planning Agent" badge. Detail
dialog: tasks with AMM codes, parts, labor, regulatory refs, agent rationale + citations, approve/reject.
`/maintenance/schedule`: Gantt of slots vs predicted failure cycles, conflict highlighting.

### 15.2.6 `/knowledge`
Search with filters (source type, ATA chapter, module), result cards with snippet + highlight +
source badge + relevance, document viewer with section navigation and chunk highlighting, corpus
stats panel (docs, chunks, tokens, last indexed).

### 15.2.7 `/analytics`
Model comparison table (arch × subset × RMSE/NASA/MAE/latency, best-in-column highlighted),
training curves, per-horizon error chart, calibration plot, fleet-level degradation cohorts,
drift monitor.

### 15.2.8 `/admin/*`
System health (service status, tick lag, queue depths, WS connections, cache hit rate),
replay console (global speed, per-engine controls, fault injection, reset), model registry with
promotion gate, agent run explorer with full trace viewer.

---

## 15.3 Component hierarchy (abridged, engine detail)

```
EngineDetailLayout
├── EngineHeader
│   ├── IdentityBlock · HealthPill · CycleProgress · ReplayControls · ModelBadge
├── EngineCanvas (client, dynamic import)
│   ├── CameraRig · Lights · Environment
│   ├── TurbofanModel
│   │   ├── Nacelle · FanRotor · Module ×6 · Nozzle · ShaftAssembly
│   │   └── Hotspot ×n
│   ├── EffectComposer (Bloom · Vignette · SMAA)
│   └── SceneHud (module label, legend, view toolbar, FPS in dev)
├── RightRail
│   ├── ComponentHealthTree · SensorGrid · AnomalyFeed · AgentInsightCards
└── TabRouter → {Overview | Sensors | PredictionXai | Components | Anomalies |
                 Timeline | Simulation | Copilot}
```

---

## 15.4 Interaction & motion inventory

| Interaction | Behaviour |
|---|---|
| Card hover | lift 2 px, border glow, prefetch data + GLB LOD1 |
| Module click (3D) | camera spring to preset, drawer slides in, breadcrumb updates |
| Band change | color tween 400 ms, one pulse ring, toast if CRITICAL |
| RUL number change | odometer roll |
| Tab switch | content cross-fade 180 ms; canvas untouched |
| Timeline scrub | 3D + sensor panels rewind to that cycle (historical mode banner shown) |
| Copilot streaming | caret shimmer, tool-call rows animate in, node pulses in graph viz |
| Simulation run | progress ring, then charts draw-in left→right |
| ⌘K | command palette: jump to engine, run scenario, ask copilot, toggle theme |
| Keyboard | `E` explode, `X` x-ray, `R` reset camera, `?` shortcuts, `/` search, `J/K` list nav |

---

## 15.5 Responsive strategy

| Breakpoint | Layout |
|---|---|
| ≥1536 | Full: sidebar + content + right rail + 3D side-by-side |
| 1280–1535 | Right rail collapses to icon tabs |
| 1024–1279 | 3D above tabs (stacked), rail becomes a drawer |
| 768–1023 | Tablet: sidebar → icon rail; fleet grid 2-up; 3D reduced quality |
| <768 | Mobile: bottom tab bar, fleet list view, 3D replaced by 2D schematic + "view in 3D" opt-in |

---

## 15.6 Accessibility
Keyboard reachable everything (3D module selection also via a listbox), visible focus rings,
`aria-live="assertive"` for CRITICAL alerts, color never the sole signal (band pills carry icons +
text), WCAG AA contrast verified in CI (axe + Lighthouse budget), full `prefers-reduced-motion`
support, chart data available as an accessible table.

---

## 15.7 Empty / loading / error / degraded states
Every surface defines all four:
- **Loading:** skeleton matching final geometry (no spinners on layout-shifting areas).
- **Empty:** illustrated, with the single next best action.
- **Error:** what failed, trace id, retry button.
- **Degraded:** explicit banners — `Reconnecting…`, `Predictions stale`, `Copilot offline mode`,
  `Simulation queued`. Degradation is *communicated*, never silent.
