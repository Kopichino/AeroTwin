# 02 — Folder Structure (Monorepo)

Tooling: **pnpm workspaces** (JS) + **uv workspace** (Python) + **Turborepo** for task graph +
**Makefile** as the human entrypoint.

```
aero-twin/
├── README.md
├── Makefile                       # make dev | seed | train | test | e2e | lint
├── docker-compose.yml             # demo profile
├── docker-compose.dev.yml         # dev profile (hot reload, jaeger, pgadmin)
├── turbo.json
├── pnpm-workspace.yaml
├── pyproject.toml                 # uv workspace root, shared tool config
├── .env.example
├── .github/
│   └── workflows/
│       ├── ci.yml                 # lint→type→test→build
│       ├── e2e.yml                # playwright against compose
│       ├── model-train.yml        # manual dispatch: retrain + register
│       └── release.yml            # tag → GHCR images
│
├── docs/                          # ← THIS DOCUMENT SET
│   ├── 00-master-index.md … 16-development-roadmap.md
│   ├── adr/ADR-001..016.md
│   ├── diagrams/*.mmd
│   ├── api/openapi.json           # generated, committed for diffing
│   └── reports/                   # model cards, eval reports, benchmark results
│
├── data/
│   ├── raw/cmapss/                # train_FD00X.txt, test_FD00X.txt, RUL_FD00X.txt
│   ├── interim/                   # parquet after parse
│   ├── processed/                 # windowed tensors, scalers (.joblib)
│   └── knowledge/
│       ├── nasa/                  # public NASA TM/CR PDFs
│       ├── faa/                   # ACs, ADs, FAR excerpts
│       ├── manuals/               # AT-9000 AMM (authored by us, markdown)
│       ├── procedures/            # SOPs, task cards, borescope guides
│       └── manifest.yaml          # source, license, version, checksum per doc
│
├── models/
│   ├── registry.json              # model registry index (hash, metrics, stage)
│   └── artifacts/{model_id}/      # weights.pt, scripted.pt, config.json, card.md
│
├── packages/                      # shared JS
│   ├── ui/                        # design system: tokens, primitives, charts
│   ├── api-client/                # GENERATED from openapi.json (do not edit)
│   ├── ws-protocol/               # zod schemas for WS envelopes (shared w/ backend via codegen)
│   ├── three-kit/                 # reusable R3F: materials, shaders, controls, loaders
│   └── config-eslint / config-ts/ # shared configs
│
├── libs/                          # shared Python
│   ├── at_core/                   # domain models (pure), enums, errors, value objects
│   │   ├── domain/{engine,twin,health,maintenance,fleet}.py
│   │   ├── events/                # event type registry + envelopes
│   │   ├── errors.py
│   │   └── clock.py
│   ├── at_contracts/              # pydantic schemas shared across services
│   ├── at_persistence/            # SQLAlchemy models, migrations, repositories base
│   ├── at_bus/                    # redis streams + pubsub abstraction
│   ├── at_observability/          # structlog, otel, metrics helpers
│   └── at_config/                 # Settings
│
├── services/
│   ├── api/
│   │   ├── src/at_api/
│   │   │   ├── main.py            # app factory, lifespan, middleware chain
│   │   │   ├── deps.py            # DI providers
│   │   │   ├── routers/           # fleet, engines, predictions, anomalies,
│   │   │   │                      # simulate, copilot, knowledge, maintenance,
│   │   │   │                      # replay, admin, health
│   │   │   ├── ws/                # gateway, channels, auth ticket, backpressure
│   │   │   ├── services/          # application services (use cases)
│   │   │   ├── repos/             # concrete repositories
│   │   │   ├── schemas/           # request/response pydantic
│   │   │   ├── security/          # jwt, rbac, rate limit
│   │   │   └── middleware/        # trace, problem-details, timing, cors
│   │   ├── tests/{unit,integration,contract}/
│   │   └── Dockerfile
│   │
│   ├── twin_engine/
│   │   ├── src/at_twin/
│   │   │   ├── main.py            # asyncio entrypoint, shard assignment
│   │   │   ├── replay/            # clock, cursor, source adapters, speed control
│   │   │   ├── registry.py        # in-memory twin registry + rehydration
│   │   │   ├── twin.py            # DigitalTwin aggregate (pure transitions)
│   │   │   ├── physics/           # efficiency proxies, component health kernel
│   │   │   ├── health/            # health index, regime detector, smoothing
│   │   │   ├── anomaly/           # residual model, EWMA, CUSUM, isolation forest
│   │   │   ├── inference_client.py
│   │   │   ├── sim/               # what-if sandbox, forked twins, scenario DSL
│   │   │   ├── persistence/       # batched event writer, snapshotter
│   │   │   └── publisher.py       # delta publication + coalescing
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   ├── inference/
│   │   ├── src/at_inference/
│   │   │   ├── main.py            # FastAPI micro-service
│   │   │   ├── runtime/           # torchscript loader, warmup, micro-batcher
│   │   │   ├── preprocess/        # scaler application, window assembly
│   │   │   ├── explain/           # integrated gradients, SHAP, attention rollout
│   │   │   └── registry_client.py
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   ├── agent_runtime/
│   │   ├── src/at_agents/
│   │   │   ├── main.py
│   │   │   ├── graph/             # LangGraph: state, nodes, edges, router, checkpointer
│   │   │   ├── agents/            # health, diagnosis, planner, copilot,
│   │   │   │                      # simulation, fleet, knowledge
│   │   │   ├── prompts/           # versioned .md prompt templates + registry
│   │   │   ├── llm/               # provider abstraction, caching, token budget
│   │   │   ├── memory/            # short-term buffer, long-term summary, entity memory
│   │   │   ├── mcp_client.py
│   │   │   ├── guards/            # injection filters, output validators, citation check
│   │   │   └── streaming.py       # token → redis pubsub
│   │   ├── evals/                 # golden-question suites, ragas configs
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   └── mcp_servers/
│       ├── twin_server/           # 9 tools over twin/fleet/history/sim
│       ├── knowledge_server/      # 5 tools over RAG
│       ├── maintenance_server/    # 6 tools over parts/slots/cost/work-orders
│       └── shared/
│
├── ml/                            # research + training (NOT shipped in serving images)
│   ├── notebooks/                 # 01-eda … 06-error-analysis (exported to html in docs/reports)
│   ├── src/at_ml/
│   │   ├── data/                  # loaders, piecewise labels, regime clustering, windowing
│   │   ├── features/              # per-regime scaler, smoothing, deltas, health-index features
│   │   ├── models/{lstm,tcn,transformer,informer,baselines}.py
│   │   ├── train/                 # trainer, cv, callbacks, seeds
│   │   ├── eval/                  # rmse, nasa_score, per-unit curves, calibration
│   │   ├── uncertainty/           # MC-dropout, deep ensemble, conformal intervals
│   │   ├── anomaly/               # autoencoder + statistical detectors
│   │   ├── explain/               # shap, IG, attention
│   │   ├── export/                # torchscript/onnx + registry write
│   │   └── config/                # hydra yaml configs per experiment
│   ├── experiments/               # mlflow or local tracking dir
│   └── tests/
│
├── rag/
│   ├── src/at_rag/
│   │   ├── ingest/                # loaders (pdf, md, html), cleaners
│   │   ├── chunk/                 # structure-aware chunking, table handling
│   │   ├── embed/                 # sentence-transformers wrapper, batching
│   │   ├── index/                 # chroma collections, metadata schema
│   │   ├── retrieve/              # hybrid BM25+dense, MMR, rerank, filters
│   │   └── eval/                  # ragas, citation precision
│   └── tests/
│
├── apps/
│   └── web/
│       ├── app/                   # Next.js App Router (see Doc 15 for full tree)
│       ├── components/
│       ├── features/              # feature-sliced: fleet, engine, copilot, sim, knowledge
│       ├── lib/                   # ws client, api client wrapper, formatters
│       ├── stores/                # zustand slices
│       ├── hooks/
│       ├── three/                 # engine model, materials, shaders, hotspots
│       ├── public/models/         # turbofan.glb + LODs, hdri
│       ├── styles/
│       ├── e2e/                   # playwright
│       └── tests/                 # vitest + RTL
│
├── infra/
│   ├── docker/                    # per-service Dockerfiles if not colocated, base images
│   ├── caddy/Caddyfile
│   ├── postgres/init/             # extensions, roles
│   ├── grafana/ + prometheus/     # optional dev observability
│   └── scripts/                   # seed.sh, wait-for.sh, backup.sh
│
└── tools/
    ├── codegen/                   # openapi→ts, pydantic→zod
    ├── loadtest/                  # locust / k6 scenarios
    └── devdata/                   # synthetic fleet generator, demo scenario scripts
```

## 2.1 Why this shape

| Choice | Justification |
|--------|---------------|
| `libs/` vs `services/` | Anything imported by ≥2 services is a library with its own tests. Prevents the classic "shared utils dumping ground". |
| `ml/` separate from `services/inference/` | Training deps (pandas, shap, matplotlib, jupyter) never enter the 400 MB serving image. Registry is the only interface. |
| `rag/` separate from `agent_runtime` | Index building is an offline batch job; retrieval is a runtime concern exposed via MCP. |
| `packages/api-client` generated | P6. CI regenerates and fails on diff → API drift is impossible. |
| `features/` in web | Feature-sliced design beats type-first folders once >30 components exist. |
| `docs/api/openapi.json` committed | Reviewable API diffs in PRs. |
| `data/knowledge/manifest.yaml` | R6 — provenance and license for every corpus document, shown in the UI's source panel. |

## 2.2 Naming and code conventions

- Python: `snake_case`, modules ≤ 400 LOC, ruff + black (line 100), mypy strict on `libs/` and `services/`.
- TypeScript: `PascalCase` components, `camelCase` hooks (`useX`), `kebab-case` files in `app/`, strict TS, no `any` (eslint error).
- Events: `domain.entity.action` past tense → `twin.health.degraded`, `agent.run.completed`.
- Redis keys: `at:{env}:{domain}:{id}[:{sub}]`.
- DB: plural snake_case tables, `id` UUIDv7 PK, `created_at/updated_at` on every table.
- Branches: `feat/m4-transformer-rul`, conventional commits, PR template with milestone checkbox.
