# 14 — Frontend State Management Architecture

## 14.1 State taxonomy

| Class | Example | Owner | Why |
|---|---|---|---|
| **Server state** | fleet page, engine detail, documents, work packages, model registry | TanStack Query | caching, dedupe, retries, invalidation |
| **Realtime state** | live twin deltas, fleet deltas, sensor ring buffers, agent token stream | Zustand | 60 Hz writes must not go through the Query cache |
| **UI state** | selected module, camera preset, drawer open, filters, sort | Zustand (persisted subset) | ephemeral, local |
| **Form state** | scenario builder, login, work-package edit | React Hook Form + Zod | validation locality |
| **URL state** | `?sort=`, `?band=`, `/engines/{id}?tab=xai` | Next.js router | shareable, back-button correct |
| **Session** | auth tokens, role | httpOnly cookie + in-memory principal | XSS safety |

**Rule:** if it comes from a REST GET and changes rarely → Query. If it arrives over WS → Zustand.
Never both as source of truth.

---

## 14.2 Zustand store composition

```
useAppStore = create<AppState>()(
  devtools(subscribeWithSelector(immer(persist(
    (...a) => ({
      ...connectionSlice(...a),
      ...fleetSlice(...a),
      ...twinSlice(...a),
      ...sensorBufferSlice(...a),
      ...selectionSlice(...a),
      ...sceneSlice(...a),
      ...copilotSlice(...a),
      ...simulationSlice(...a),
      ...notificationSlice(...a),
      ...preferencesSlice(...a),
    }),
    { name:'aerotwin', partialize: s => ({ preferences: s.preferences, scene: s.scene.presets }) }
  ))))
)
```

### Slice contracts

**connectionSlice** — `status: 'connecting'|'open'|'degraded'|'closed'`, `latencyMs`,
`reconnectAttempt`, `subscribedChannels: Map<string, number /*refcount*/>`,
actions `subscribe/unsubscribe/setStatus/recordPong`.

**fleetSlice** — `engines: Map<EngineId, FleetRow>`, `counts`, `avgHealth`, `lastUpdate`,
`applyFleetDelta(delta)`, `applyFleetSnapshot(snap)`. Kept as a `Map` so a delta is O(changed),
not O(fleet). Sorting is a **derived selector**, memoized on `(sortKey, order, filterHash, version)`
where `version` bumps only when a ranked field changes.

**twinSlice** — `twins: Map<EngineId, TwinLive>` for engines currently open (detail page, hovered
card, comparison set). Bounded LRU of 8 entries so memory can't grow unbounded.

**sensorBufferSlice** — per engine, per sensor **ring buffers** (`Float32Array(600)` + head index).
This is the single most important performance decision on the frontend: charts read a typed array
slice; no array allocation per frame, no GC churn.

**selectionSlice** — `selectedEngineId`, `selectedModule`, `hoveredModule`, `comparisonSet`,
`timelineCursor` (for scrubbing history).

**sceneSlice** — `cameraPreset`, `exploded`, `xray`, `autoRotate`, `quality: 'low'|'high'`,
`fanSpeedTarget`, `frameloop: 'always'|'demand'`.

**copilotSlice** — `conversations`, `activeRunId`, `streamingText`, `steps[]`, `toolCalls[]`,
`citations[]`, `isStreaming`, `degraded`. Token deltas append to a plain string, flushed to React
on rAF, so a 60-token/s stream causes ≤ 60 commits/s but only ~1 render/frame.

**simulationSlice** — `activeSimId`, `progress`, `baseline`, `scenario`, `delta`, `draftScenario`.

**notificationSlice** — bounded deque (100), unread count, severity filter, `markRead`.

**preferencesSlice** — theme, units (°R/K), density, reduced motion override, default sort,
persisted to localStorage.

---

## 14.3 WS → store bridge

```
WebSocketProvider
  └── FrameRouter
        ├── rAF batching queue (drain once per animation frame)
        ├── zod parse + discriminated dispatch
        ├── high-frequency types → Zustand slice actions (direct, immer-free fast path)
        └── low-frequency types → Zustand + throttled queryClient.setQueryData (≤2 Hz)
```

Selective invalidation map:

| Frame | Zustand action | Query effect |
|---|---|---|
| `twin.delta` | `twinSlice.applyDelta`, `sensorBufferSlice.push` | patch `['engine', id]` at ≤2 Hz |
| `fleet.delta` | `fleetSlice.applyFleetDelta` | patch `['fleet', filters]` at ≤2 Hz |
| `twin.event` | `twinSlice.pushEvent` | `invalidate ['timeline', id]` (debounced 2 s) |
| `twin.anomaly` | `twinSlice.pushAnomaly` + notification | `invalidate ['anomalies', id]` |
| `agent.token` | `copilotSlice.appendToken` | none |
| `agent.completed` | `copilotSlice.finalize` | `invalidate ['conversation', id]`, `['runs']` |
| `sim.completed` | `simulationSlice.setResult` | `invalidate ['simulation', id]` |
| `notification` | `notificationSlice.push` | `invalidate ['work-packages']` if WP-related |

**Why the 2 Hz patch cap:** the Query cache backs paginated tables and detail panels that re-render
whole subtrees. Zustand backs the numbers that change every frame. Bridging them at 2 Hz keeps both
correct without either dominating the main thread.

---

## 14.4 Selector discipline

```ts
// ❌ subscribes to the entire map → re-renders on any engine change
const engines = useAppStore(s => s.fleet.engines);

// ✅ scalar subscription, memo-stable
const hi = useAppStore(useShallow(s => s.twins.get(id)?.healthIndex));

// ✅ derived, memoized outside React
const sortedIds = useFleetSorted(sortKey, order, filters); // reselect-style memo on version
```
Enforced by an ESLint rule + a code-review checklist item. Dev builds run React Scan to surface
accidental broad subscriptions.

---

## 14.5 Optimistic updates & rollback

| Action | Optimistic | Rollback trigger |
|---|---|---|
| Pause/resume replay | flip `status` immediately | `twin.command.rejected` frame or 4xx |
| Acknowledge anomaly | mark acked, fade row | mutation error → restore + toast |
| Approve work package | move card to APPROVED column | 409 → return card + explain conflict |
| Rename conversation | inline | error → revert |

Implemented with TanStack `onMutate/onError/onSettled` and a Zustand `pendingOps` set that the UI
renders as a subtle shimmer.

---

## 14.6 Query configuration

```ts
defaultOptions: {
  queries: { staleTime: 30_000, gcTime: 5*60_000, retry: 2,
             refetchOnWindowFocus: false,   // WS keeps us fresh; refetch storms are worse
             throwOnError: false },
  mutations: { retry: 0 }
}
```
Prefetching: hovering a fleet card prefetches `['engine', id]` and warms the GLB.
Suspense boundaries per panel so a slow history query never blanks the 3D view.

---

## 14.7 Memory bounds (explicit, because this streams forever)

| Structure | Bound | Eviction |
|---|---|---|
| `fleet.engines` | 300 rows | none (fleet is fixed size) |
| `twins` | 8 engines | LRU on route change |
| sensor ring buffers | 600 samples × 12 sensors × 8 engines ≈ 230 KB | fixed-size typed arrays |
| notifications | 100 | FIFO |
| copilot messages | 50 in memory, older paged from API | — |
| agent tool-call trace | 200 per run | truncate with "show all" fetch |

A memory soak test (30 min stream) asserts heap growth < 15 MB — part of the M8 exit criteria.

---

## 14.8 Testing state
Pure slice reducers unit-tested with Vitest (no React) · bridge tested with a mock WS emitting
recorded frame fixtures · selector memoization asserted by render-count tests · integration tests
verify that a `twin.delta` produces exactly one commit per rAF.
