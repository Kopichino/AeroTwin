# M2 — Data Layer & Ingestion — Completion Report

**Status:** complete, awaiting approval
**Commit:** `282dbab` · 181 tests passing · all 8 architecture contracts kept

---

## (a) What was built

| Component | Detail |
|---|---|
| **`at_data.acquire`** | Automatic download (option (a)) from the official NASA PHM S3 mirror, SHA-256 pinning, nested-zip extraction, and **structural validation** of row/unit/column counts and shortest trajectory before declaring success. Idempotent. |
| **`at_data.parse`** | Raw whitespace text → typed `float32/int32` DataFrame → zstd Parquet. Piecewise RUL labels capped at 125 (ADR-012) for train and test. |
| **`at_data.regimes`** | Deterministic k-means++ (no sklearn dependency), canonical centroid ordering for stable regime ids, silhouette scoring, JSON persistence for reuse by the twin engine. |
| **`at_data.eda`** | Generates `docs/reports/eda.md` — a committed, diffable, regenerable report rather than a notebook. |
| **`at_persistence`** | 7 ORM tables from Doc 04 + dialect-portable `GUID`/`JSONBType` so tests run containerless **without weakening the Postgres DDL**. |
| **Tooling** | `make data`, `make data-verify`, `make eda`; 3 new import-linter contracts. |

---

## (b) How to run it

```bash
make data          # download + verify + parquet + regime models  (~15 s warm)
make data-verify   # validate on-disk dataset without downloading
make eda           # regenerate docs/reports/eda.md
make check         # all gates
```

---

## (c) Evidence against M2 exit criteria

| Exit criterion | Target | Result |
|---|---|---|
| Units loadable | 709 train + 707 test | ✅ **709 / 707** (1,416 trajectories, 265,256 rows) |
| EDA report committed | yes | ✅ `docs/reports/eda.md`, regenerable |
| Regime clustering validated | silhouette > 0.95 | ✅ **0.9997** (FD002 and FD004) |
| Schema implemented | Doc 04 tables | ✅ 7 tables, native `UUID`/`JSONB`/enum DDL asserted by test |
| Tests | grow suite | ✅ **181** (was 135); `at_core` still **100 %** |
| Type safety | strict | ✅ mypy clean, 36 files |
| Boundaries | enforced | ✅ **8/8** contracts kept |

**Correctness verified against ground truth, not just self-consistency:**
- FD001 unit 1 final-cycle RUL computes to **112**, matching line 1 of NASA's `RUL_FD001.txt`.
- Recovered FD002 regime centroids are physically meaningful flight conditions — sea level, 10/20/25/35/42 kft — not arbitrary partitions.
- Degradation signature matches turbofan physics: T30 **+2.7σ**, T50 **+3.5σ**, Ps30 **+3.7σ** rise toward failure while coolant bleeds W31 **−3.0σ**, W32 **−3.4σ** fall. This is the empirical basis for the Doc 08 §8.4 efficiency proxies.

**ADR-014 quantified.** For T30 in FD002:

| measure | value |
|---|---|
| \|corr(raw, RUL)\| | 0.0324 |
| \|corr(per-regime z-scored, RUL)\| | **0.6180** |
| improvement | **19.1×** |

Between-regime spread of means (119.7) is **21×** the mean within-regime std (5.7). Without per-regime normalization the degradation signal is effectively invisible.

---

## (d) Two planning corrections forced by measurement

**1. ADR-013 was wrong for FD004.** I had specified `window=20` for both 6-condition subsets on the standard assumption that the shortest test trajectory is 21 cycles. Direct measurement:

| subset | shortest test trajectory | old window | corrected |
|---|---:|---:|---:|
| FD002 | 21 | 20 | 20 ✓ |
| FD004 | **19** | 20 ✗ | **18** |

Two FD004 units would have been unscoreable. Docs 00/01/07 amended; `Subset.window_size` corrected; a test now asserts `window_size <= min_test_trajectory` for **every** subset so this cannot regress.

**2. The constant-sensor list from the literature is wrong.** The commonly cited seven-sensor drop list (`s1,s5,s6,s10,s16,s18,s19`) does not hold per subset:

- **`s10` (epr)** is constant in FD001 but takes 4 distinct values in FD003 with **|corr(RUL)| = 0.49**. Dropping it discards real degradation signal.
- **`s6` (P15)** is *near*-constant, not constant — excluded on a signal basis, not a variance basis.

`at_core.domain.sensors` now encodes measured per-subset sets: **FD001 14, FD002 21, FD003 15, FD004 21** informative sensors.

Both corrections are documented inline in the EDA report under "Planning correction" callouts, so the reasoning is visible to an examiner.

---

## (e) Known gaps (deliberate, scheduled)

| Gap | Milestone |
|---|---|
| Alembic migrations + hypertable/compression DDL not yet applied | M3 (needs a live Postgres) |
| Repositories, `UnitOfWork`, testcontainers integration tests | M3 |
| Seeder (fleets, engines, users) not written | M3 — pairs naturally with twin provisioning |
| `/fleet`, `/engines/{ref}` still unimplemented | M3, once repositories exist |
| Readiness still reports Postgres `skipped` | M3 |

I deliberately moved the read-only REST endpoints to M3. Building them now against repositories that have no live database would mean writing them twice; they land alongside the twin engine that populates their data.

---

## (f) Plan for M3 — Digital Twin Core + Streaming ⭐

The keystone milestone. Everything so far has been groundwork for this.

1. **Alembic migrations** — full Doc 04 DDL: enum types, hypertables, compression, continuous aggregates, retention.
2. **Repositories + UnitOfWork**, integration-tested against testcontainers Postgres+TimescaleDB.
3. **Seeder** — provision 1,416 engines across fleets, with randomized phase offsets.
4. **Replay engine** — virtual clock, per-unit cursors, speed control, seek, drift correction.
5. **Physics kernel** — the efficiency proxies from Doc 08 §8.4, now grounded in the σ-shifts measured in M2.
6. **Twin registry** — sharding, Redis leases, crash recovery via snapshot + event replay.
7. **Redis bus** — command stream, coalescing publisher.
8. **WebSocket gateway** — ticket auth, channels, snapshot-then-delta, backpressure.
9. **Terminal dashboard** — proves the engine works before any UI exists.

**Exit criteria:** 260 twins ticking at 8× for 30 min with p99 tick < 120 ms · `kill -9` recovery preserves state hash · deterministic replay.

**Environment note:** this sandbox has no Docker, so Postgres/Redis integration tests will be written against testcontainers and verified in CI, while I validate logic locally with SQLite and a fakeredis in-process double. I'll flag clearly which evidence is local versus CI-only in the M3 report.
