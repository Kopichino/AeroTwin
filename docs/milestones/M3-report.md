# M3 — Digital Twin Core — Partial Completion Report

**Status:** core complete, **streaming deferred** — awaiting your decision
**Commit:** `fe5505d` · 275 tests passing · 9 architecture contracts kept

---

## (a) What was built

| Component | Detail |
|---|---|
| **`at_twin.physics`** | 11 thermodynamic proxies (HPC temp ratio, discharge pressure, bleed enthalpy, EGT, coolant bleeds, fuel ratio, bypass ratio, fan speed divergence, core-speed droop) → **per-regime** healthy baselines → logistic health map → 0–100 per module with named drivers. |
| **`at_twin.replay`** | Virtual clock (0.5×–32×) with speed-banking, pause/resume/seek, drift-corrected; `TelemetrySource` interface with C-MAPSS and synthetic implementations; banded fleet age mix. |
| **`at_twin.registry`** | Single-writer twin registry, deterministic engine ids, sharding, per-twin epochs, command FSM handling, end-of-life recycling, snapshot cadence, typed `FleetSummary`. |
| **`at_twin.monitor`** | Live ANSI terminal fleet dashboard — the M3 deliverable that proves the engine works before any UI exists. |

Run it: `make monitor`

---

## (b) Evidence

### Fault-mode recovery against NASA ground truth
The strongest validation available — the kernel is judged against the documented fault mode, not its own arithmetic:

| Subset | Documented fault | HPC identified as worst module |
|---|---|---|
| FD001 | HPC degradation | **72 %** |
| FD003 | HPC + Fan | **86 %** |
| FD002 | HPC degradation | **73 %** |
| FD004 | HPC + Fan | **86 %** |

### NFR-1 performance (260 twins, FD002, 8×)
| metric | value | budget |
|---|---|---|
| p50 tick | 7.7 ms | — |
| p99 tick | **25.0 ms** | 120 ms ✅ |
| max tick | 27.5 ms | — |

### Determinism (ADR-004)
Two independent 200-tick runs produce **identical state hashes** (`9fcf0cfd5bc329bd`) and identical event counts. Event-sourced replay is exact.

---

## (c) Four real bugs, all found by validating against data rather than by testing my own assumptions

**1. Core-speed proxy had an inverted sign.** I used the `Nc/NRc` ratio for gas-path droop. Correcting for inlet conditions *cancels* the droop and flips it: the ratio **rises** 0.11 % while raw `Nc` **falls** 0.11 %. The kernel was reporting an improving nozzle on a failing engine. Now uses raw `Nc`, with a regression test.

**2. Pooled baselines measured flight condition, not wear.** The physics kernel kept one baseline per engine. In FD002 the six regimes move T30 by 350 °R and fuel ratio by 4×, while a *whole life* of degradation moves T30 by ~14 °R. Result: the combustor looked worst on ~90 % of FD002 engines. Baselines are now **per regime**, with the fitted M2 centroids wired in. HPC attribution went 9 % → 73 %.

This is the same effect I quantified in M2's EDA (ADR-014) — I documented it for the ML pipeline and then built the physics kernel as if it didn't apply. Worth noting because it's exactly the kind of error that survives to a viva.

**3. An untrusted placeholder carried 40 % of the health index.** The pre-M5 RUL estimate is a crude trend extrapolation, yet `W_MODEL` gave it 40 % weight — dragging engines with ~90 % component health into WARNING. `HealthInputs.model_trusted` now redistributes that weight to the physics terms until a real model lands in M5.

**4. Recycled engines never aged again.** All twins shared one global cycle counter, so a recycled twin sat permanently behind it. Each twin now tracks its own `epoch_cycle`. This is what produces a genuine **standing fleet** — 260 active indefinitely, avg HI stable ~59 over 1,200 ticks — instead of a fleet that dies once and stays dead.

---

## (d) What I did NOT build, and why I'm stopping to ask

M3 as scoped in Doc 16 also included **Alembic migrations, repositories, the Redis bus, the WebSocket gateway, and crash recovery**. I built the compute core and stopped.

**The reason: this sandbox has no Docker, Postgres, or Redis.** I could write all of that code, but I could not run any of it. Everything above is verified by execution against the real dataset; the persistence and streaming layers would be verified by nothing but my own reading of the code. Given that M1 and M2 each surfaced real bugs *only* when I ran things, shipping ~1,500 lines of unexecuted infrastructure and calling it done would be the least trustworthy thing I could do.

The twin core is also the part that had to be right first — it's the source of truth every other layer merely transports.

### Options

| Option | What happens |
|---|---|
| **A — Recommended.** In-process bus + WS, defer Postgres | Implement the bus as an async in-memory broker behind the same interface, wire the FastAPI WebSocket gateway, and get **live streaming to a browser** verified end to end here. Redis/Postgres adapters slot in behind the same ports when you have Docker. |
| **B** | Write the full Redis + Postgres + Alembic layer now, unexecuted, verified only in CI when you run it. |
| **C** | Skip to **M4 (ML models)** — fully verifiable here — and do all persistence/streaming together later against a real stack. |

I recommend **A**: it keeps the "everything shipped is executed" standard, unblocks M6's frontend, and the port/adapter boundary is already in the architecture (Doc 05 §5.1).

---

## (e) Deferred to the persistence milestone

| Item | Note |
|---|---|
| Alembic migrations, hypertables, compression, retention | Needs a live Postgres |
| Repositories, `UnitOfWork`, testcontainers integration tests | Needs a live Postgres |
| Redis Streams command bus, coalescing publisher | Interface designed; adapter pending |
| WebSocket gateway (ticket auth, channels, backpressure) | Depends on the bus |
| Crash recovery via snapshot + event replay | `rehydrate()` exists and is tested in-process; the disk round-trip is not |
| Seeder | Trivial once repositories exist |

---

## (f) Cumulative state

| Metric | M1 | M2 | M3 |
|---|---|---|---|
| Tests | 135 | 181 | **275** |
| Architecture contracts | 5 | 8 | **9** |
| `at_core` coverage | 100 % | 100 % | 100 % |
| mypy strict | clean | clean | clean (41 files) |

Which option would you like?
