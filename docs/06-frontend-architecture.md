# 06 — Frontend Architecture

Stack: **Next.js 15 (App Router) · React 19 · TypeScript strict · Tailwind CSS v4 · Framer Motion ·
React Three Fiber + drei + postprocessing · Zustand · TanStack Query v5 · Radix UI · visx/Recharts ·
Zod**.

---

## 6.1 Rendering strategy

| Route class | Strategy | Why |
|---|---|---|
| Marketing/landing `/` | Static (SSG) | Instant load, good first impression |
| Auth `/login` | Server component shell + client form | — |
| Fleet `/fleet` | Server shell (layout, nav) + client data grid | Live WS data must be client-side |
| Engine `/engines/[id]` | Server shell + client canvas/panels; `generateMetadata` for title | 3D and WS are client-only |
| Copilot `/copilot` | Client, streaming via WS | — |
| Knowledge `/knowledge` | Server-rendered search results (RSC fetch) + client highlight | SEO-irrelevant but cheap and fast |
| Admin `/admin/*` | Client | — |

No SSR of live telemetry — the shell renders instantly with skeletons; data arrives over WS.

---

## 6.2 Application shell

```
RootLayout
 ├── ThemeProvider (dark default, CSS vars)
 ├── QueryProvider (TanStack, 30s stale, retry 2, suspense)
 ├── WebSocketProvider (single multiplexed connection, channel refcounting)
 ├── StoreHydrator (Zustand ← initial REST payload)
 ├── ToastProvider / CommandPalette (⌘K)
 └── AppFrame
      ├── Sidebar (collapsible, icon rail)
      ├── TopBar (global search, replay controls, connection pill, user menu)
      └── <main> {children} </main>
```

**One WebSocket for the whole app.** `WebSocketProvider` owns the socket; components call
`useChannel('twin:{id}')` which refcounts subscriptions and unsubscribes on unmount (Doc 13).

---

## 6.3 Feature-sliced structure

```
features/
├── fleet/
│   ├── api/            # query hooks (useFleetQuery, useFleetSummary)
│   ├── components/     # FleetGrid, EngineCard, FleetTable, RiskMatrix, FleetKpiStrip, FilterBar
│   ├── model/          # selectors, sorting/priority logic (pure, unit-tested)
│   └── index.ts
├── engine/
│   ├── api/
│   ├── components/     # EngineHeader, SensorGrid, SensorSparkline, HistoryChart,
│   │                   # PredictionCard, RulTimeline, ComponentHealthTree,
│   │                   # AnomalyFeed, XaiPanel, EventTimeline
│   ├── three/          # EngineScene, TurbofanModel, FanRotor, ModuleMesh, Hotspot,
│   │                   # HeatMaterial, ExplodedView, CameraRig, SceneHud
│   └── model/
├── copilot/            # ChatPanel, MessageBubble, ToolTraceViewer, CitationChip,
│                       # AgentGraphViz, SuggestedPrompts, StreamingMarkdown
├── simulation/         # ScenarioBuilder, ParameterSlider, ComparisonChart, DeltaSummary,
│                       # ScenarioLibrary
├── knowledge/          # SearchBar, ResultList, DocViewer, HighlightOverlay, SourceBadge
├── maintenance/        # WorkPackageBoard, PackageCard, ApprovalDialog, CostBreakdown
└── admin/              # ReplayConsole, ModelRegistryTable, SystemHealth, EventStreamTail
```

Rule: features may import from `packages/ui` and `lib/`, **never from each other**. Cross-feature
composition happens only in `app/` route files.

---

## 6.4 Design system (`packages/ui`)

### Tokens (CSS custom properties, Tailwind v4 `@theme`)
```
--bg-base:        #07080B
--bg-elevated:    #0D0F14
--bg-glass:       rgba(255,255,255,0.04)
--border-subtle:  rgba(255,255,255,0.08)
--border-strong:  rgba(255,255,255,0.14)
--text-primary:   #F2F4F8
--text-secondary: #9BA3B4
--text-tertiary:  #5C6475

--accent:         #4F8DFD     /* electric blue — brand */
--accent-glow:    #4F8DFD33
--health-good:    #22C98A
--health-watch:   #7FD1A6
--health-warn:    #F5B942
--health-crit:    #FF4D4D
--anomaly:        #A855F7

radius: 6 / 10 / 16 / 24
shadow-glass: 0 1px 0 rgba(255,255,255,.05) inset, 0 20px 60px rgba(0,0,0,.5)
blur-glass: 18px
font: Inter var (UI) · JetBrains Mono (numerics/telemetry)
```

### Motion language (Framer Motion)
- Page transitions: fade + 8 px rise, 240 ms, `[0.16,1,0.3,1]` easing.
- Card hover: `scale 1.012`, border brightens, 160 ms.
- Number changes: `AnimatePresence` odometer roll for RUL/health.
- Status change: color tween 400 ms + one pulse ring for CRITICAL.
- **Respect `prefers-reduced-motion`**: all of the above collapse to instant.

### Primitives
`Button`, `IconButton`, `Card`, `GlassPanel`, `Badge`, `HealthPill`, `Sparkline`, `Gauge`,
`StatTile`, `DataTable` (TanStack Table + virtualization), `Drawer`, `Dialog`, `Tabs`, `Tooltip`,
`Select`, `Slider`, `Toast`, `Skeleton`, `EmptyState`, `ErrorState`, `Kbd`, `CommandPalette`.

Accessibility: Radix under everything, focus-visible rings, WCAG AA contrast, all charts have a
`<table>` fallback in an accessible details element, live regions for critical alerts.

---

## 6.5 Data fetching contract

- **TanStack Query** = server state (lists, details, history, documents, work packages).
  Query keys: `['fleet', filters]`, `['engine', id]`, `['history', id, span]`, `['predictions', id]`.
- **Zustand** = realtime + UI state (see Doc 14).
- Bridge: WS delta arrives → Zustand updates (60 Hz safe) → *and* `queryClient.setQueryData` is
  patched **at most 2 Hz** for the affected keys so cached lists don't go stale.
- Generated client from OpenAPI (`packages/api-client`) — typed, no manual fetch calls.
- Optimistic updates for: pause/resume, acknowledge anomaly, approve work package.

---

## 6.6 Performance plan

| Concern | Technique |
|---|---|
| 260 fleet cards | TanStack Virtual windowing; card is `React.memo` with a custom comparator on `(health, rul, status)` |
| High-frequency numbers | Values written to a Zustand slice with `subscribeWithSelector`; leaf components subscribe to a single scalar |
| Charts | visx + canvas renderer for >2 k points; ring buffer of 600 samples in the store |
| 3D | See §6.7 |
| Bundle | Route-level code splitting; `three` + R3F dynamically imported only on the engine route; target initial JS < 180 KB gz |
| Images/GLB | Draco-compressed GLB (<1.5 MB), KTX2 textures, preloaded on fleet-card hover |
| Re-render audit | React Scan / why-did-you-render in dev; CI budget check with `next build --profile` |

---

## 6.7 3D subsystem (React Three Fiber)

### Scene graph
```
<Canvas dpr={[1,2]} gl={{antialias:true, powerPreference:'high-performance'}} shadows="soft">
  <CameraRig>                       // PerspectiveCamera + OrbitControls (damped) + presets
  <Environment preset="warehouse" /> // HDRI, low intensity
  <Lights>                          // key + rim + hemi; emissive does most of the work
  <Suspense fallback={<SkeletonEngine/>}>
    <TurbofanModel>
      <Nacelle />                   // semi-transparent shell, toggled by ExplodedView
      <FanRotor  rpm={nf} />        // useFrame rotation, motion-blur-ish via shader
      <Module id="LPC" health={..}/>
      <Module id="HPC" health={..}/>
      <Module id="COMBUSTOR" .../>
      <Module id="HPT" .../>
      <Module id="LPT" .../>
      <Nozzle />
      <ShaftAssembly />
      <Hotspot module="HPC" anomaly={...}/>   // billboarded marker, pulses
    </TurbofanModel>
  </Suspense>
  <EffectComposer>
    <Bloom intensity={0.6} luminanceThreshold={0.55} />
    <Vignette /> <SMAA />
  </EffectComposer>
  <SceneHud />   // html overlay: module label, health %, RUL, legend
</Canvas>
```

### Health→visual mapping
```
color  = lerp3(crit#FF4D4D → warn#F5B942 → good#22C98A, health/100)
emissiveIntensity = 0.15 + 0.85 * (1 - health/100)      // sicker = glows hotter
pulseHz           = health < 35 ? 1.6 : 0
roughness         = 0.35 + 0.4 * (1 - health/100)        // degraded looks rougher/sooty
```
Implemented in a custom `HeatMaterial` (extends `MeshStandardMaterial` via `onBeforeCompile`) so the
transition is a shader uniform tween, not a per-frame material rebuild.

### Fan RPM
`nf = sensor_8` → normalize by regime-specific `[min,max]` → `rpmNorm ∈ [0,1]` →
`rotationSpeed = lerp(2.0, 9.0, rpmNorm) rad/s`, eased over 600 ms on change so it never snaps.
At high speed a radial blur alpha ring is faded in to sell the motion without costing frames.

### Interaction
- Click a module → raycast pick → sets `selectedModule` in store → camera flies to a preset
  (`useSpring` on position + target, 800 ms) → right drawer opens with that module's sensors,
  health history, related anomalies, and AMM tasks.
- `E` toggles exploded view (modules translate along +Z with staggered spring).
- `X` toggles X-ray (nacelle opacity 1 → 0.15).
- `R` resets camera. Presets: Overview / Cutaway / Hot Section / Fan.

### Asset & performance
- One Draco GLB, 7 named meshes matching `engine_module` enum. LOD0 (~120 k tris) for detail view,
  LOD1 (~15 k) for fleet hover preview.
- `useGLTF.preload()` on fleet route.
- `frameloop="demand"` when the tab is idle/no animation; back to `always` while streaming.
- Pause `useFrame` work when `document.hidden`.
- Fallback: if WebGL2 unavailable → 2D SVG schematic with identical health coloring (never a blank box).

---

## 6.8 Error, loading, empty states
Every route has `loading.tsx` (skeleton matching final layout) and `error.tsx` (recoverable, with
`reset()`), plus a global `ErrorBoundary` reporting to `/api/v1/telemetry/client-error`.
Degraded modes are explicit UI, not silence: `Disconnected — reconnecting (3)`,
`Prediction stale (last 12s)`, `Copilot running in offline mode`.

---

## 6.9 Frontend testing
Vitest + RTL for components/hooks · MSW for API mocks · a mock WS server for stream tests ·
Storybook for the design system with a11y addon · Playwright for 8 E2E journeys ·
Chromatic-style visual snapshots on the design system (optional) · R3F tested via
`@react-three/test-renderer` for scene-graph assertions (no pixel testing).
