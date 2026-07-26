# M5 — Inference Integration, Anomaly Detection & XAI — Completion Report

**Status:** complete, awaiting approval
**Commit:** `505b67c` · 423 tests passing · 11 architecture contracts kept

> The dashboard's RUL column is no longer a placeholder.

---

## (a) What was built

| Component | Detail |
|---|---|
| **`at_twin.anomaly`** | EWMA z-score + decaying CUSUM + multivariate RMS fusion over per-regime residuals. Welford running statistics (no per-sensor history buffers), confirmation window, resolve hysteresis, module attribution via the Doc 08 matrix. |
| **`at_twin.inference`** | TorchScript loading from the registry, split-conformal prediction intervals, failure probabilities at 30/60/90 cycles, gradient×input attribution, circuit breaker with last-good fallback. |
| **Registry** | Entries now carry a fitted `conformal_offset`. |
| **Twin engine** | Scores the whole fleet in **one batch per tick**; `model_trusted=True` so RUL finally drives the health index. |
| **API + dashboard** | Fleet rows expose `rul_p10/p50/p90`, `model_backed`, `anomaly_score`, `anomaly_alerting`; dashboard shows intervals, anomaly severity chips and a model-coverage KPI. |

---

## (b) Evidence

### Anomaly detection vs NASA ground truth (60 units per subset)

| metric | FD001 | FD003 |
|---|---:|---:|
| Detection rate | **100 %** | **100 %** |
| Lead time before EOL (median) | **132 cycles** | **148 cycles** |
| Lead time (p10) | 88 cycles | 110 cycles |
| False positives (healthy first 20 %) | **6.0 %** | **8.5 %** |
| Module attribution = HPC | **100 %** | **100 %** |

Both subsets have a documented HPC fault mode, and the detector agrees on every unit.

### Uncertainty calibration

| | before | after |
|---|---:|---:|
| PICP (nominal 80 %) FD001 | 46.9 % | **80.0 %** |
| PICP FD003 | — | 71.0 % |

### Performance

| metric | budget | measured |
|---|---|---|
| Tick p99, 260 twins | 120 ms | **106.8 ms** |
| Inference, batched | 15 ms/engine (NFR-2) | **~0.3 ms/engine** |
| Inference p50 latency | — | 5.2 ms/batch |

---

## (c) Four bugs found by measurement

**1. CUSUM ran away to 291σ.** CUSUM is unbounded and assumes a step change against a stationary process. Gas-path degradation is *persistent*, so the statistic saturated within a few dozen cycles and reported CRITICAL forever — a **68 % false-positive rate** on healthy engines. Fixed with per-cycle decay (0.92) plus a ceiling, turning it into a detector of *recent* sustained shift.

**2. 2σ thresholds fire constantly.** The fused score is a max over 3 detectors × 21 sensors. With that many chances, a textbook single-channel 2σ rule alerts on healthy engines 57–80 % of the time. Thresholds were re-derived from the *measured* healthy-score distribution rather than convention.

**3. Per-engine inference blew the tick budget.** Scoring engines one at a time serialised 100+ forward passes per tick: **p99 2270 ms against a 120 ms budget**. Windows are now collected during the loop and scored in a single batch — which is also how the model was trained to run. → **103 ms**.

**4. MC-dropout on a traced module is pure waste.** TorchScript *tracing* folds dropout away, so 20 stochastic samples returned 20 identical values — 21× the compute for zero information (21 ms → 1 ms per batch). Disabled on the streaming path; the interval now comes from conformal calibration, which is distribution-free and doesn't assume the model can self-report its own uncertainty.

Also fixed: recycled twins inherited the previous engine's sensor history (feeding the model a window spanning two engines), and `attribute_to_modules` was computed twice per detection (the single largest cost in the tick loop).

---

## (d) Honest limitations

- **FD003 PICP is 71 %, not 80 %.** The conformal offset was fitted on training units; FD003's two fault modes make residuals less exchangeable between calibration and test. Fixable with per-fault-mode calibration — recorded, not hidden.
- **Attribution is gradient×input, not Integrated Gradients.** IG needs ~50 passes per prediction, which doesn't fit the tick budget. Gradient×input preserves the *ranking* the UI consumes. Full IG and SHAP remain for the on-demand "explain deeply" path (M7).
- **Anomaly evaluation has no labels.** C-MAPSS provides none. The protocol — lead time, FPR on the healthy fifth, attribution vs documented fault mode — is stated openly rather than dressed up as supervised accuracy.
- **Model-backed coverage is 92/100**, not 100. Freshly recycled engines need `window` cycles of history before they can be scored; until then they show a trend estimate marked with `*`.

---

## (e) Cumulative state

| Metric | M1 | M2 | M3 | M4 | M5 |
|---|---|---|---|---|---|
| Tests | 135 | 181 | 319 | 372 | **423** |
| Contracts | 5 | 8 | 10 | 11 | **11** |
| mypy strict | clean | clean | clean | clean | clean (58 files) |

---

## (f) Next: M6 — Frontend Foundation + Fleet Dashboard

The backend is now feature-complete enough to justify the real UI: Next.js app, design system in Storybook, typed WebSocket client with reconnect, Zustand slices, virtualized fleet grid, and the mission-control overview.

The current `/dashboard` is a single hand-written HTML file — deliberately, so streaming was visible from M3. M6 replaces it with the production frontend from Doc 06/14/15.
