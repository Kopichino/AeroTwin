# How to run AeroTwin

Three ways to see it, fastest first.

---

## 1. Instant preview — no install

Open either file in any browser:

- **`docs/preview/fleet-ui-preview.html`** — the Next.js fleet UI
- **`docs/preview/engine-detail-preview.html`** — engine detail with charts and XAI
- **`docs/preview/engine-3d-preview.html`** — the 3D digital twin view
- **`docs/preview/dashboard-preview.html`** — the zero-dependency fallback

A static snapshot of the real dashboard with genuine twin state baked in: 260
FD002 engines after 700 replay ticks, physics-derived component health, and the
worst-module attribution. Nothing to install, nothing to run.

---

## 2. The real UI (Next.js) — recommended

Two terminals:

```bash
# terminal 1 — backend, twins, websocket
make install && make data && make demo

# terminal 2 — frontend
make web-install && make web
```

Open **http://localhost:3000/fleet**.

Sortable fleet table, band filters, search, anomalies-only toggle, live KPI
tiles and a connection badge that shows reconnect attempts honestly.

Click any row to open the engine detail view: an interactive 3D turbofan whose
modules are coloured by component health and whose fan turns at the real fan
speed, alongside health and anomaly history, the RUL timeline with its conformal
80% band, per-sensor sparklines, and the attribution panel explaining what drives
the prediction.

In the 3D view: drag to orbit, scroll to zoom, `E` for exploded view, `X` for
X-ray. Modules are also selectable from the chips below the canvas, which keeps
the view usable by keyboard and screen reader. If WebGL is unavailable the same
information renders as a 2D cross-section.

`make web-check` runs typecheck, unit tests and a production build.

---

## 3. Backend only — Python, no Node

```bash
cd aero-twin
make install        # one time: venv + workspace packages  (~40 s)
make data           # one time: download + verify C-MAPSS  (~15 s, 12 MB)
make demo           # starts everything
```

Then open:

| URL | What it is |
|---|---|
| **http://localhost:8000/dashboard** | Zero-dependency fallback dashboard (no Node needed) |
| http://localhost:8000/docs | Interactive OpenAPI explorer |
| http://localhost:8000/api/v1/fleet | Raw fleet JSON |
| http://localhost:8000/api/v1/knowledge/search?q=borescope | Knowledge search |
| http://localhost:8000/health/ready | Readiness probe |

`make demo` runs the API, the twin engine and the WebSocket gateway in one
process. 260 digital twins tick at 8x, physics-informed health updates stream to
the browser at 4 Hz per engine, and the fleet rollup at 1 Hz.

**Without the dataset?** Set `AT_TWIN_SYNTHETIC=true` and it runs on generated
telemetry with the same code path — useful on a machine with no internet.

### Terminal view

```bash
make monitor        # ANSI fleet dashboard, no browser needed
```

---

## 4. Docker

```bash
docker compose up --build
```

Open http://localhost:8000/dashboard. Single container; the twin engine runs
inside the API process using the in-memory bus.

For the multi-process topology with Postgres and Redis:

```bash
docker compose --profile full up --build
```

That starts TimescaleDB and Redis alongside. The bus adapter is selected in the
composition root (`services/api/src/at_api/main.py`), so switching from the
in-memory bus to Redis is one line — the twin engine, gateway and routers are
unchanged. **This is why option A was not a shortcut:** the port boundary was in
the architecture from Doc 05 §5.1, and both adapters are implemented.

---

## What you are looking at

The dashboard shows a **standing fleet** of 260 virtual turbofan engines
replaying NASA C-MAPSS trajectories:

- **Health index** — fused from physics proxies, worst-component score and
  anomaly pressure, EWMA-smoothed with a monotonic-decay constraint so gas-path
  wear never spontaneously heals.
- **Worst module** — derived from thermodynamic efficiency and flow-capacity
  proxies (HPC temperature ratio, EGT, coolant bleed drift), each measured as a
  deviation from that engine's *own* healthy baseline, **per operating regime**.
- **Bands** — HEALTHY ≥ 80, WATCH ≥ 60, WARNING ≥ 35, CRITICAL below, with
  3-cycle hysteresis so the grid does not flicker.
- **RUL** — currently a trend extrapolation, explicitly *not* trusted by the
  health index. The trained model lands in M4 and this becomes a real prediction.

Engines that reach end of trajectory are replaced with a fresh install and a new
tail number, so the fleet keeps a realistic age mix indefinitely rather than
dying out.

---

## Common commands

```bash
make rag-index      # build the knowledge index, print statistics
make rag-eval       # regenerate docs/reports/rag-eval.md
make help           # every target
make check          # lint + typecheck + architecture contracts + 319 tests
make test           # tests only
make eda            # regenerate docs/reports/eda.md
make openapi        # regenerate the committed API schema
make data-verify    # validate the dataset on disk
```

---

## Troubleshooting

**Port 8000 in use**
`.venv/bin/uvicorn at_api.main:app --port 8080`

**`make data` fails / no internet**
Run with `AT_TWIN_SYNTHETIC=true make demo` — the platform falls back to
generated telemetry automatically if the dataset is missing.

**Dashboard shows "reconnecting"**
The API is not up yet. Check `curl localhost:8000/health/live`.

**Fleet all HEALTHY at first**
Expected. Engines start staggered across their life and degrade as they tick;
give it 30–60 seconds at 8x, or raise `AT_REPLAY_SPEED` to 16 or 32.
