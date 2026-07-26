# AeroTwin — Agentic Digital Twin Platform for Aircraft Engine Predictive Maintenance

**Codename:** `aero-twin`
**Dataset:** NASA C-MAPSS Turbofan Engine Degradation Simulation (FD001–FD004)
**Document set version:** 1.0 (Architecture Freeze Candidate)
**Status:** PLANNING PHASE — no implementation code authorized yet.

---

## 0.1 Purpose of this document set

This is the **Software Design Specification (SDS)** for the capstone. It is written to the standard
expected of an internal platform-engineering RFC at a company like GE Aerospace Digital, Siemens
Xcelerator, or NVIDIA Omniverse. Nothing here is aspirational filler — every artifact maps to a
concrete deliverable in the roadmap (Doc 16).

The guiding principle:

> **This is a distributed systems + AI platform engineering project that happens to contain ML models.
> It is not an ML notebook with a UI bolted on.**

The examiner should walk away believing a small product team shipped this, not that a student
trained an LSTM.

---

## 0.2 Document map

| # | Document | Covers | Freeze gate |
|---|----------|--------|-------------|
| 00 | `00-master-index.md` | This file. Vision, principles, glossary, NFRs, risk register | M0 |
| 01 | `01-architecture.md` | C4 model, deployment topology, tech decisions + ADRs | M0 |
| 02 | `02-folder-structure.md` | Monorepo layout, every directory justified | M0 |
| 03 | `03-module-breakdown.md` | Every module, its contract, owner, dependencies | M0 |
| 04 | `04-database-schema.md` | PostgreSQL DDL, TimescaleDB, Redis keyspace, ChromaDB collections | M0 |
| 05 | `05-backend-architecture.md` | FastAPI layering, DI, services, workers, error model | M0 |
| 06 | `06-frontend-architecture.md` | Next.js App Router, rendering strategy, 3D subsystem | M0 |
| 07 | `07-ai-pipeline.md` | Data prep, 4 RUL models, anomaly, XAI, MLOps, registry | M1 |
| 08 | `08-digital-twin-architecture.md` | Twin state machine, physics-informed component health, replay clock | M1 |
| 09 | `09-multi-agent-architecture.md` | 7 agents, LangGraph topology, MCP tool servers, memory | M1 |
| 10 | `10-data-flow-diagrams.md` | DFD L0/L1/L2 | M0 |
| 11 | `11-sequence-diagrams.md` | 12 canonical sequences | M0 |
| 12 | `12-api-documentation.md` | Full REST surface, schemas, errors, versioning | M0 |
| 13 | `13-websocket-plan.md` | Channels, envelope protocol, backpressure, reconnect | M0 |
| 14 | `14-state-management.md` | Zustand slices, TanStack Query, WS→store reducer bridge | M0 |
| 15 | `15-ui-page-hierarchy.md` | Route tree, component tree, design system tokens | M0 |
| 16 | `16-development-roadmap.md` | M0–M12 milestones, exit criteria, approval gates | M0 |

**Reading order for a reviewer:** 00 → 01 → 10 → 08 → 09 → 07 → 05 → 12 → 13 → 06 → 14 → 15 → 04 → 02 → 03 → 11 → 16.

---

## 0.3 Product vision

AeroTwin is a **fleet-scale digital twin control plane**. An airline reliability engineer opens it
in the morning and sees 100–260 virtual turbofan engines flying. Each one:

- streams sensor telemetry in accelerated wall-clock time,
- maintains a server-side twin object that is the single source of truth for its health,
- is scored continuously by a deep RUL model with calibrated uncertainty,
- is watched by an anomaly detector operating on residuals against a nominal baseline,
- is explained by SHAP-style sensor attributions,
- is reasoned about by a graph of seven cooperating LLM agents,
- is rendered as an interactive, animated 3D turbofan whose modules glow green→amber→red.

The engineer can then ask, in natural language, *"Why is Engine 27 degrading faster than the fleet
median, and can it complete a 50-cycle rotation?"* — and receive a grounded, cited, auditable answer
plus a draft maintenance work package.

---

## 0.4 Non-negotiable architectural principles

| # | Principle | Consequence |
|---|-----------|-------------|
| P1 | **The Twin is the source of truth, not the model** | Models are *sensors on* the twin. Twin state survives model swaps. |
| P2 | **Event-sourced twin state** | Every state transition is an append-only event. Timeline & replay are free. |
| P3 | **Agents never touch the DB directly** | Agents only call MCP tools. Tools are the security + audit boundary. |
| P4 | **LLM is replaceable and optional** | Deterministic fallbacks for every agent. Demo must survive an API outage. |
| P5 | **No secrets, no live network in the hot path** | Inference is local PyTorch. LLM calls are async, off the streaming path. |
| P6 | **Everything typed end to end** | Pydantic v2 → OpenAPI 3.1 → generated TypeScript client. Zero hand-written DTOs. |
| P7 | **Backpressure over buffering** | WS drops/coalesces frames rather than growing unbounded queues. |
| P8 | **Simulation ≠ mutation** | What-if runs execute on a forked twin in a sandbox; never mutate live state. |
| P9 | **Explainability is a first-class artifact** | Every prediction row persists its attribution vector. |
| P10 | **Reproducibility** | Seeded training, pinned deps, model registry with hashes, `docker compose up` reproduces everything. |

---

## 0.5 Glossary

| Term | Definition |
|------|-----------|
| **C-MAPSS** | Commercial Modular Aero-Propulsion System Simulation. NASA turbofan degradation dataset. |
| **Unit** | One engine trajectory in C-MAPSS (`unit_number`). Maps 1:1 to a Twin. |
| **Cycle** | One flight cycle = one row of C-MAPSS telemetry. Our atomic time step. |
| **RUL** | Remaining Useful Life, in cycles, until functional failure. |
| **Twin** | Server-side stateful object mirroring one engine. |
| **Twin Event** | Immutable record of a twin state transition (append-only). |
| **Replay Clock** | Virtual clock mapping wall-clock ms → engine cycles at a speed multiplier. |
| **Operating Condition Regime** | Cluster of `(altitude, Mach, TRA)` — 1 regime in FD001/FD003, 6 in FD002/FD004. |
| **Module** | Physical engine section: Fan, LPC, HPC, Combustor, HPT, LPT, Nozzle. |
| **Health Index (HI)** | Normalized 0–100 twin health scalar. |
| **Piecewise RUL** | Standard C-MAPSS label with RUL capped at `R_early` (=125). |
| **MCP** | Model Context Protocol — how agents access tools/resources. |
| **Copilot** | Conversational agent surface in the UI. |
| **Work Package** | Structured maintenance recommendation artifact produced by the Planner agent. |

---

## 0.6 Dataset facts the architecture must respect

| Subset | Train units | Test units | Conditions | Fault modes |
|--------|------------:|-----------:|-----------:|-------------|
| FD001 | 100 | 100 | 1 (sea level) | 1 (HPC degradation) |
| FD002 | 260 | 259 | 6 | 1 (HPC degradation) |
| FD003 | 100 | 100 | 1 | 2 (HPC + Fan) |
| FD004 | 249 | 248 | 6 | 2 (HPC + Fan) |

**Verified against the actual NASA files in M2** (`make data`), not quoted from literature:

| Subset | Train rows | Test rows | Train min/max len | **Test min len** | RUL labels |
|---|---:|---:|---:|---:|---:|
| FD001 | 20,631 | 13,096 | 128 / 362 | 31 | 100 |
| FD002 | 53,759 | 33,991 | 128 / 378 | **21** | 259 |
| FD003 | 24,720 | 16,596 | 145 / 525 | 38 | 100 |
| FD004 | 61,249 | 41,214 | 128 / 543 | **19** | 248 |

Total 245,256 rows across 1,416 engine trajectories.

Each row: `unit_number, time_in_cycles, op_setting_1..3, sensor_1..21`.

**Sensor map** (needed for XAI naming and 3D module attribution):

| # | Symbol | Description | Units | Module |
|---|--------|-------------|-------|--------|
| 1 | T2 | Total temp at fan inlet | °R | Fan |
| 2 | T24 | Total temp at LPC outlet | °R | LPC |
| 3 | T30 | Total temp at HPC outlet | °R | HPC |
| 4 | T50 | Total temp at LPT outlet | °R | LPT |
| 5 | P2 | Pressure at fan inlet | psia | Fan |
| 6 | P15 | Total pressure in bypass duct | psia | Fan/Bypass |
| 7 | P30 | Total pressure at HPC outlet | psia | HPC |
| 8 | Nf | Physical fan speed | rpm | Fan |
| 9 | Nc | Physical core speed | rpm | HPC/HPT |
| 10 | epr | Engine pressure ratio (P50/P2) | — | Global |
| 11 | Ps30 | Static pressure at HPC outlet | psia | HPC |
| 12 | phi | Fuel flow / Ps30 | pps/psi | Combustor |
| 13 | NRf | Corrected fan speed | rpm | Fan |
| 14 | NRc | Corrected core speed | rpm | HPC |
| 15 | BPR | Bypass ratio | — | Fan/Bypass |
| 16 | farB | Burner fuel-air ratio | — | Combustor |
| 17 | htBleed | Bleed enthalpy | — | HPC |
| 18 | Nf_dmd | Demanded fan speed | rpm | Control |
| 19 | PCNfR_dmd | Demanded corrected fan speed | rpm | Control |
| 20 | W31 | HPT coolant bleed | lbm/s | HPT |
| 21 | W32 | LPT coolant bleed | lbm/s | LPT |

Sensors 1, 5, 6, 10, 16, 18, 19 are constant in FD001/FD003 → dropped by the variance filter, but
**retained** for FD002/FD004 where regime variation makes them informative. The feature pipeline is
therefore **subset-aware** (see Doc 07 §7.2).

**Fan RPM for the 3D animation** comes from sensor 8 (`Nf`), normalized against its regime-specific
range → drives `fanSpeed` uniform in the shader (Doc 06 §6.7).

---

## 0.7 Non-functional requirements (measurable)

| ID | Requirement | Target | Verified in |
|----|-------------|--------|-------------|
| NFR-1 | Fleet tick throughput | ≥ 260 twins @ 1 tick/s sustained, p99 tick latency < 120 ms | M5 load test |
| NFR-2 | RUL inference latency | p95 < 15 ms/engine on CPU (batched) | M4 bench |
| NFR-3 | WS fan-out | 50 concurrent clients, < 250 ms server→paint | M5 |
| NFR-4 | 3D render | ≥ 55 FPS on integrated GPU, 1080p, single-engine view | M8 |
| NFR-5 | Fleet grid | 260 cards, virtualized, < 16 ms frame during stream | M8 |
| NFR-6 | Copilot first token | < 2.5 s (streamed) | M10 |
| NFR-7 | RAG grounding | ≥ 0.85 citation-precision on 40-question eval set | M9 |
| NFR-8 | RUL accuracy | FD001 RMSE ≤ 14.0, NASA Score ≤ 350 | M4 |
| NFR-9 | Cold start | `docker compose up` → seeded, browsable UI in < 5 min | M11 |
| NFR-10 | Availability of demo | Full UI functional with `LLM_PROVIDER=none` | M10 |
| NFR-11 | Test coverage | Backend ≥ 80 % line, agents ≥ 70 %, critical paths 100 % | M11 |
| NFR-12 | API contract | OpenAPI 3.1 published, TS client generated in CI, drift = build failure | M3 |

---

## 0.8 Out of scope (explicitly)

- Real aircraft connectivity / ARINC 429 / ACARS ingestion.
- Multi-tenant billing, org management, SSO (a single-tenant RBAC stub exists).
- Kubernetes/Helm production deploy (Docker Compose + one Fly.io/Render profile only).
- Fine-tuning an LLM. We use prompt engineering + RAG + tools.
- Mobile native apps (the web UI is responsive down to tablet).

---

## 0.9 Risk register

| ID | Risk | Prob | Impact | Mitigation |
|----|------|:----:|:------:|-----------|
| R1 | LangGraph agent loops burn tokens / hang | M | H | Hard `recursion_limit`, per-run token budget, 25 s wall timeout, deterministic fallback path (P4) |
| R2 | 3D scene tanks FPS with 260 engines | M | H | Fleet view uses 2D cards + instanced mini-meshes; full 3D only on detail route |
| R3 | Transformer overfits small C-MAPSS train set | H | M | Aggressive regularization, 5-fold CV, early stop, ensemble with TCN, report honest CV variance |
| R4 | WS message storm at 32× speed | H | M | Server-side coalescing to 4 Hz max per channel + delta encoding |
| R5 | Scope creep kills timeline | H | H | Milestone gates with your explicit approval; each milestone independently demoable |
| R6 | RAG corpus legally unusable | M | M | Use only public-domain NASA TM/CR reports, FAA ACs/ADs, EASA public docs, plus a synthesized fictional "AT-9000 Engine Maintenance Manual" we author |
| R7 | LLM provider cost/quota | M | M | Provider abstraction: OpenAI / Groq / Ollama local / `none`. Response cache keyed by prompt hash |
| R8 | Digital-twin physics looks fake to examiner | M | H | Ground component health in real thermodynamic proxies (efficiency & flow deltas), document derivation |
| R9 | Postgres write amplification from tick events | M | M | TimescaleDB hypertable + batched COPY writer + 7-day retention on raw telemetry |
| R10 | Time — solo/small team | H | H | M0–M6 is the defensible core; M7–M12 are additive. Cut order documented in Doc 16 §16.14 |

---

## 0.10 Approval gate

**Nothing in `apps/` or `services/` is written until Doc 01–06, 10–16 are approved by you.**
Approval is per-milestone. See Doc 16 for the gate checklist.
