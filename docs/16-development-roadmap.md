# 16 — Development Roadmap

**Cadence:** 13 milestones (M0–M12). Each is independently demoable and ends at an **approval gate**.
No code for milestone N+1 is written until you approve milestone N.

Estimates assume ~15–20 focused hours/week for one strong developer; scale accordingly for a team.

---

## Milestone map

| M | Name | Duration | Demoable outcome |
|---|---|---|---|
| M0 | Architecture & Planning | 1 wk | This document set, approved & frozen |
| M1 | Foundation & Skeleton | 1 wk | `docker compose up` → empty but wired system, CI green |
| M2 | Data Layer & Ingestion | 1 wk | 260 engines seeded, telemetry queryable, EDA report |
| M3 | Digital Twin Core + Streaming | 2 wks | Fleet ticking in real time, deltas over WS, terminal dashboard |
| M4 | ML — RUL Models & Comparison | 2 wks | 4 architectures trained, comparison report, best model registered |
| M5 | Inference Integration + Anomaly + XAI | 1.5 wks | Live RUL, intervals, anomalies, attributions on the stream |
| M6 | Frontend Foundation + Fleet Dashboard | 2 wks | Real dashboard, live, beautiful, responsive |
| M7 | Engine Detail + Charts | 1.5 wks | All tabs except 3D/sim/copilot working |
| M8 | 3D Digital Twin Visualization | 2 wks | The showpiece: interactive turbofan reacting to live health |
| M9 | RAG Knowledge Base | 1.5 wks | Corpus indexed, hybrid search UI, eval numbers |
| M10 | Multi-Agent System + Copilot | 2.5 wks | 7 agents, LangGraph, MCP, streaming chat + trace viewer |
| M11 | Simulation + Maintenance Workflow | 1.5 wks | What-if engine, work packages, approval loop |
| M12 | Hardening, Deployment, Docs, Defense | 2 wks | Load/chaos tested, deployed, report + video + viva pack |

**Total ≈ 21–22 weeks.** Cut order for compression is in §16.14.

---

## M0 — Architecture & Planning ✅ (current)
**Deliverables:** Docs 00–16, 16 ADRs, mermaid diagram sources, risk register, NFR table.
**Exit criteria:** you approve the architecture; ADRs frozen; NFR targets accepted; repo initialized
with docs only.
**Gate question to you:** *Approve, or request changes to any of: tech choices, twin design, agent
topology, milestone ordering, NFR targets?*

---

## M1 — Foundation & Skeleton
- Monorepo scaffold exactly as Doc 02; pnpm + uv workspaces; Turbo pipeline; Makefile.
- `docker-compose.dev.yml`: postgres+timescale, redis, chroma, api, twin-engine, inference,
  agent-runtime, web, jaeger.
- FastAPI app factory with the full middleware chain, `/health/*`, OpenAPI publishing.
- Alembic baseline migration; `at_core` domain skeleton; `at_bus`; `at_observability`.
- Next.js app with AppFrame shell, design tokens, 6 primitives in Storybook.
- CI: ruff, black, mypy, eslint, tsc, pytest, vitest, import-linter, openapi drift check.
- Pre-commit hooks, conventional commits, PR template.

**Exit:** `make dev` boots all 8 services; `/health/ready` green; CI passes on a trivial PR;
Storybook renders the token palette. **Demo:** empty shell, connection pill shows "connected".

---

## M2 — Data Layer & Ingestion
- Full schema from Doc 04, including hypertables, compression, continuous aggregates, retention.
- C-MAPSS parser → parquet; idempotent seeder creating fleets, engines, users, model placeholders.
- Repositories + UoW + integration tests on testcontainers.
- EDA notebook → `docs/reports/eda.md`: trajectory lengths, sensor variance, regime clustering
  validation, degradation patterns, correlation heatmaps, fault-mode differences across subsets.
- Read-only REST: `/fleet`, `/engines/{ref}`, `/engines/{ref}/telemetry`.

**Exit:** 709 train + 707 test units loadable; `/fleet` returns real rows; queries p95 < 80 ms;
EDA report committed. **Demo:** Swagger UI browsing real data + the EDA report.

---

## M3 — Digital Twin Core + Streaming ⭐
- `at_twin.twin` pure aggregate + FSM + event catalogue (Doc 08 §8.7).
- Replay clock, per-unit cursors, fleet phase offsets, speed control, seek.
- Physics proxies + component health kernel + HI fusion + hysteresis.
- Redis Streams command consumer, Pub/Sub publisher with coalescing.
- Batched persistence writer + snapshotter + crash recovery.
- WS gateway: ticket auth, channels, subscribe/snapshot/delta, heartbeat, backpressure.
- Property-based tests on transitions; determinism test; recovery test.

**Exit:** 260 twins tick at 8× for 30 min with p99 tick < 120 ms; kill -9 recovery preserves state
hash; a CLI/terminal dashboard shows live health. **Demo:** terminal UI streaming the fleet — proves
the engine works before any pixel of UI exists.

---

## M4 — ML: RUL Models & Comparison ⭐
- Data module: piecewise labels, regime clustering, per-regime scaling, feature engineering, windows, GroupKFold.
- Baselines + LSTM + TCN + Transformer + Informer, Hydra configs, 3 seeds each, 4 subsets.
- Evaluation harness: RMSE, NASA score, MAE, R², per-horizon buckets, lead-time analysis.
- Ensemble via NNLS; uncertainty (MC-dropout + conformal); calibration plots.
- TorchScript export, registry write, auto-generated model cards.
- `docs/reports/model-comparison.md` with honest ablations (features, window size, architecture).

**Exit:** NFR-8 met on FD001 (RMSE ≤ 14, score ≤ 350); all 4 subsets reported; production model
promoted in the registry; export smoke test in CI. **Demo:** comparison report + a notebook showing
predicted vs actual degradation curves.

---

## M5 — Inference Integration + Anomaly + XAI
- `inference` service: TorchScript loader, adaptive micro-batching, warmup, `/predict/batch`, `/explain`.
- twin-engine inference client with circuit breaker + last-good fallback + `stale` flag.
- Anomaly stack: residual EWMA + CUSUM + autoencoder, fusion, module attribution, events.
- XAI: Integrated Gradients online (every 10 cycles), KernelSHAP on demand + cache, attention rollout.
- Persist predictions + attributions + anomalies; REST endpoints for all three.

**Exit:** NFR-2 met (p95 < 15 ms batched); anomalies fire with measurable lead time before EOL and
< 5 % FPR on the healthy first third; explanations persisted for every prediction.
**Demo:** live stream now carrying RUL + intervals + anomaly flags + top-5 attributions.

---

## M6 — Frontend Foundation + Fleet Dashboard ⭐
- Design system completed in Storybook (all primitives, a11y checks).
- WS client: multiplexing, refcounted channels, rAF batching, resync, backoff.
- Zustand slices per Doc 14; TanStack Query setup; generated API client wired.
- `/overview` mission control and `/fleet` (grid + table, filters, sorting, virtualization).
- Quick-look drawer via intercepted route; notifications; command palette; auth flow.

**Exit:** NFR-5 (260 cards, no dropped frames), memory soak < 15 MB growth, Lighthouse ≥ 90,
axe clean. **Demo:** a genuinely good-looking live fleet dashboard.

---

## M7 — Engine Detail + Charts
- Engine layout shell with persistent canvas slot (placeholder until M8).
- Tabs: Overview, Sensors, Prediction & XAI, Components, Anomalies, Timeline.
- Chart kit: live line/area with ring buffers, brush-zoom, overlay compare, uncertainty bands,
  anomaly shading, attribution bars, temporal saliency heatstrip.
- Timeline scrubber with historical-state replay into the panels.

**Exit:** every number on screen traceable to an API field; charts hold 60 FPS during streaming.
**Demo:** full single-engine analysis workflow.

---

## M8 — 3D Digital Twin Visualization ⭐⭐ (the showpiece)
- Source/author a turbofan GLB with 7 named meshes; Draco + KTX2; LOD0/LOD1.
- `three-kit`: `HeatMaterial`, camera rig with presets & spring flights, hotspot billboards,
  exploded view, x-ray, selection raycasting, HUD overlay.
- Bind twin state → color/emissive/roughness/pulse; `Nf` → fan rotation with easing + blur ring.
- Failure, maintenance, and regime visual states; `frameloop="demand"` idling; WebGL2 fallback to
  2D SVG schematic.

**Exit:** NFR-4 (≥55 FPS on integrated GPU), module click → camera + drawer < 900 ms, no memory
leak across 50 navigations, fallback verified. **Demo:** the screenshot that sells the project.

---

## M9 — RAG Knowledge Base
- Corpus assembly with `manifest.yaml` (licenses); author the AT-9000 AMM (ATA 71/72/73/75/77/79)
  with realistic task cards and fault-isolation trees; synthesize fleet-history narratives.
- Ingest → structure-aware chunking → bge embeddings → Chroma collections + `doc_chunks` in PG.
- Hybrid retrieval (FTS ∪ dense, RRF) + cross-encoder rerank + metadata filters.
- `/knowledge` search UI + document viewer with highlighting; corpus stats.
- Eval: 40 golden questions, ragas + citation precision → `docs/reports/rag-eval.md`.

**Exit:** NFR-7 (citation precision ≥ 0.85), retrieval p95 < 400 ms, ≥ 150 documents/2000 chunks.
**Demo:** ask a maintenance question, get the exact AMM section back.

---

## M10 — Multi-Agent System + Copilot ⭐⭐
- Three MCP servers with 20 tools, JSON-Schema validated, persisted call log.
- LangGraph: state, 7 agent nodes, router, critic, synthesizer, guards, Postgres checkpointer.
- LLM provider abstraction (OpenAI/Groq/Ollama/Null) + cache + budgets + streaming.
- Deterministic fallbacks for every agent; degraded-mode UI.
- Autonomous triggers (§9.11): band change → diagnosis → draft work package.
- Copilot UI: streaming markdown, citation chips, tool-trace viewer, live agent graph, inspector.
- Eval suite + CI regression gate.

**Exit:** NFR-6 (first token < 2.5 s), intent accuracy ≥ 0.9 on the golden set, zero ungrounded
citations, full run works with `LLM_PROVIDER=none`. **Demo:** the four required questions answered
with visible multi-agent reasoning.

---

## M11 — Simulation + Maintenance Workflow
- Sandbox forking, degradation propagation, Monte-Carlo paths, scenario DSL + validation.
- Simulation API, WS progress, persistence, templates.
- Scenario builder UI + baseline-vs-scenario comparison + delta verdict cards.
- Simulation agent wiring (NL → ScenarioSpec → interpretation).
- Work-package board, approval flow, scheduling Gantt, maintenance effect applied to the twin.

**Exit:** simulations reproducible with a seed, complete < 3 s for 50 paths × 100 cycles;
approval → twin health visibly restored + timeline entry. **Demo:** "what if we delay 40 cycles?"
answered quantitatively, then a work package approved and executed.

---

## M12 — Hardening, Deployment, Documentation, Defense
- Load test (NFR-1/3), chaos suite (kill services, Redis flap, inference 500s, toxiproxy latency).
- Coverage to NFR-11; Playwright E2E for 8 journeys; schemathesis fuzzing.
- Security pass: authz matrix tests, rate limits, prompt-injection red-team set, dependency audit.
- Production compose + one cloud profile; GitHub Actions release pipeline; seeded demo volume.
- Deliverables: README with architecture diagrams, `docs/reports/*` (EDA, model comparison, RAG eval,
  agent eval, benchmarks), model cards, 5-minute demo video, 20-slide defense deck, viva Q&A pack.

**Exit:** all NFRs verified with evidence in `docs/reports/benchmarks.md`; cold start < 5 min;
one-command demo. **Demo:** the full 12-minute guided walkthrough.

---

## 16.13 Cross-cutting definition of done (every milestone)
1. Code merged via PR with review checklist.
2. Unit + integration tests for new logic; coverage not decreased.
3. Types complete (mypy strict / no `any`).
4. OpenAPI + generated client regenerated if the API changed.
5. Docs updated (this set is living).
6. Observability: new spans/metrics for new hot paths.
7. Demo script updated in `docs/demo-script.md`.
8. No TODOs without an issue link.

## 16.14 Compression / cut order (if time runs short — R10)
Cut in this order, never earlier items first:
1. Informer (keep LSTM/TCN/Transformer) — M4
2. FD002/FD004 (keep FD001/FD003) — M4
3. KernelSHAP deep explain (keep IG) — M5
4. `/analytics` page — M7
5. Exploded/X-ray views (keep color + fan + hotspots) — M8
6. Maintenance Gantt (keep kanban) — M11
7. Fleet Management + Simulation agents (keep 5 agents) — M10
8. Chaos suite (keep load test) — M12

**Never cut:** the twin core (M3), one strong RUL model (M4), live streaming to UI (M6),
3D health visualization (M8), the agent graph with ≥5 agents + RAG (M9/M10). Those five are the
project's identity.

## 16.15 Approval protocol
At each gate I will post: (a) what was built, (b) how to run it, (c) evidence against the exit
criteria, (d) known gaps, (e) the plan for the next milestone. You reply **APPROVE** or
**CHANGES: …**. I do not start the next milestone without that.
