# M1 — Foundation & Skeleton — Completion Report

**Status:** complete, awaiting approval
**Commit:** `66774fe`  ·  75 files  ·  135 tests passing

---

## (a) What was built

| Component | Detail |
|---|---|
| **Monorepo scaffold** | `libs/` (4 shared libraries) · `services/` (4 runtime processes) · `docs/` · `tools/` · `infra/` · `data/` · `models/`, matching Doc 02 |
| **`at_core`** — domain kernel | `enums` (Subset/TwinStatus/HealthBand/Severity/EngineModule/CommandType/FaultMode/ReplaySpeed) · `sensors` (21-sensor catalogue + attribution matrix + criticality weights) · `health` (HI fusion, EWMA, monotonic-decay constraint, band hysteresis) · `fsm` (data-driven transition table) · `twin` (immutable aggregate + pure transitions) · `events` (22-member catalogue) · `errors` (RFC 9457 hierarchy) |
| **`at_config`** | Pydantic `BaseSettings`, `AT_` prefix, 50+ validated fields, frozen, namespaced Redis key builder |
| **`at_observability`** | structlog JSON/console renderers, service stamping, stdlib bridge |
| **`at_api`** | App factory · middleware chain (trace → CORS → gzip → timing) · problem-details handlers · `/health/{live,ready,deep}` · DI providers · 12 documented OpenAPI tags |
| **Quality gates** | ruff (lint+format) · mypy strict · import-linter (5 contracts) · pytest |
| **CI** | 4 jobs: lint/typecheck, tests (with Postgres+Redis services), OpenAPI drift check, image build + container smoke test |
| **Containers** | `docker-compose.dev.yml` (10 services, milestone-gated profiles) · multi-stage Dockerfile (base/dev/runtime, non-root, healthcheck) |
| **Tooling** | `Makefile` (22 targets) · `export_openapi.py` with `--check` drift mode · `.env.example` · Postgres init SQL |

---

## (b) How to run it

```bash
cd aero-twin
make install         # venv + all workspace packages
make check           # lint + typecheck + arch + tests  (the full CI gate)
make api             # http://localhost:8000/docs
make dev             # full Docker stack (requires Docker)
```

---

## (c) Evidence against M1 exit criteria

| Exit criterion | Result | Evidence |
|---|---|---|
| Services boot, `/health/ready` green | ✅ | Live uvicorn: `200` in **8.6 ms**; ready reports all 3 dependencies |
| CI passes | ✅ | `make check` → all gates pass |
| Domain kernel 100 % coverage | ✅ | **100.00 %** on `at_core` (423 statements, 0 missed), enforced by `--cov-fail-under=100` |
| Full-suite coverage | ✅ | **99 %** overall (797 statements, 7 missed) |
| Type safety | ✅ | `mypy --strict` clean on 28 source files |
| Architectural boundaries | ✅ | 5/5 import-linter contracts KEPT |
| OpenAPI published | ✅ | `docs/api/openapi.json` committed; drift check verified to fail on tampering |
| Error contract | ✅ | Verified live: `content-type: application/problem+json`, correct `code`/`type`/`trace_id` |

**Verifications performed beyond the checklist:**
- **Negative test of the architecture guard** — injected `import redis` into `at_core.domain.health`; contract flipped to BROKEN; removed it; back to KEPT. The guard is real, not decorative.
- **Negative test of OpenAPI drift** — tampered with the committed schema; `--check` exited 1. CI will genuinely catch API drift.
- **Real HTTP server** (not just `TestClient`) — verified trace propagation, `Server-Timing`, gzip, CORS preflight, and 404 problem shape.

**Two real bugs found and fixed during verification:**
1. **PEP 563 route-model resolution** — a Pydantic model declared inside a test fixture silently bound to `fastapi.Body` because `from __future__ import annotations` makes FastAPI resolve annotations against *module* globals. Collapsed the request body into one field. Fixed by moving the model to module scope; documented in the test so it isn't reintroduced.
2. **Missing `py.typed` markers** — all four packages lacked PEP 561 markers, so mypy skipped cross-package analysis entirely (25 phantom errors masking real ones). Added markers plus `mypy_path` for the src-layout workspace; two genuine type errors then surfaced in `twin.py` and were fixed properly rather than silenced.

---

## (d) Known gaps (deliberate, scheduled)

| Gap | Milestone |
|---|---|
| Postgres/Redis/inference report `skipped` in readiness | M2 / M3 / M5 — they report honestly rather than faking `ok` |
| No database schema or migrations yet | M2 |
| `twin_engine`, `inference`, `agent_runtime` are directory stubs | M3 / M5 / M10 |
| No frontend yet (`apps/web` not scaffolded) | M6 |
| Auth, rate limiting middleware not mounted | M6 |
| Docker images unbuilt locally (no Docker in this environment) | Validated by CI; compose YAML parse-verified |

---

## (e) Plan for M2 — Data Layer & Ingestion

1. Full Doc 04 schema as Alembic migrations: hypertables, compression, continuous aggregates, retention policies.
2. C-MAPSS parser → Parquet; idempotent seeder (fleets, engines, users, model placeholders).
3. `at_persistence`: SQLAlchemy 2 async models, `BaseRepository`, `UnitOfWork`, Timescale mixin.
4. Repository integration tests against testcontainers Postgres+TimescaleDB.
5. EDA notebook → `docs/reports/eda.md`: trajectory lengths, sensor variance, **regime-clustering validation** (silhouette > 0.95 claim from Doc 07 must be proven), degradation curves, fault-mode differences across subsets.
6. Read-only REST: `/fleet`, `/engines/{ref}`, `/engines/{ref}/telemetry`.

**Exit criteria:** 709 train + 707 test units loadable · `/fleet` returns real rows · query p95 < 80 ms · EDA report committed.

**Input needed from you:** the C-MAPSS dataset files. I can either (a) have M2 auto-download from the NASA PHM repository, or (b) have you drop `train_FD00X.txt` / `test_FD00X.txt` / `RUL_FD00X.txt` into `data/raw/cmapss/`. I'll also build a synthetic generator with the same statistical profile so development and CI never block on data availability.
