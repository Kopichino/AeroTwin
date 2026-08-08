# AeroTwin — Agentic Digital Twin Platform for Aircraft Engine Predictive Maintenance

> NASA C-MAPSS · Digital Twin · Deep RUL Prediction · Explainable AI · LangGraph Multi-Agent · MCP · React Three Fiber

**Status: M0 — Architecture & Planning. No implementation code yet, by design.**

## What this is

A fleet-scale digital twin control plane for turbofan engines. C-MAPSS trajectories are replayed as
live telemetry; each engine is mirrored by an event-sourced server-side twin; deep models predict
Remaining Useful Life with calibrated uncertainty; anomaly detectors and explainability run on the
stream; seven LangGraph agents reason over the fleet through MCP tools and a grounded RAG corpus;
and a React Three Fiber turbofan visualizes module-level health in real time.

## Documentation set

Read in this order:

| Doc | Title |
|---|---|
| [00](docs/00-master-index.md) | Master index — vision, principles, NFRs, glossary, risks |
| [01](docs/01-architecture.md) | Software architecture — C4, processes, ADRs |
| [02](docs/02-folder-structure.md) | Monorepo folder structure |
| [03](docs/03-module-breakdown.md) | Module breakdown & contracts |
| [04](docs/04-database-schema.md) | Database schema (Postgres/Timescale, Redis, Chroma) |
| [05](docs/05-backend-architecture.md) | Backend architecture |
| [06](docs/06-frontend-architecture.md) | Frontend architecture |
| [07](docs/07-ai-pipeline.md) | AI/ML pipeline (RUL, anomaly, XAI, RAG) |
| [08](docs/08-digital-twin-architecture.md) | Digital Twin architecture |
| [09](docs/09-multi-agent-architecture.md) | Multi-agent architecture (LangGraph + MCP) |
| [10](docs/10-data-flow-diagrams.md) | Data flow diagrams (L0/L1/L2) |
| [11](docs/11-sequence-diagrams.md) | Sequence diagrams (12 flows) |
| [12](docs/12-api-documentation.md) | REST API documentation |
| [13](docs/13-websocket-plan.md) | WebSocket communication plan |
| [14](docs/14-state-management.md) | Frontend state management |
| [15](docs/15-ui-page-hierarchy.md) | UI page hierarchy & design spec |
| [16](docs/16-development-roadmap.md) | Development roadmap (M0–M12) |

## Next action

Review Doc 00 and Doc 01 first, then approve or request changes at the **M0 gate**
(see [Doc 16 §16.15](docs/16-development-roadmap.md)).

##KOPPESH P
