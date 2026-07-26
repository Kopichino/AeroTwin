# M4 — RUL Models & Comparison — Completion Report

**Status:** complete, awaiting approval
**Commit:** `8c4c9c9` · 372 tests passing · 11 architecture contracts kept

---

## (a) What was built

| Component | Detail |
|---|---|
| **`at_ml.data`** | Per-regime scaler (fit on train only), sliding windows, **unit-grouped** splits, piecewise labels capped at 125. |
| **`at_ml.models`** | LSTM (bi-directional + attention pooling), TCN (dilated causal residual), Transformer (pre-LN + CLS), **Informer** (ProbSparse attention + distilling), CNN and MLP baselines. All share `(B,W,F) → (B,)` with a softplus head. |
| **`at_ml.train`** | Huber loss (δ=5), AdamW, cosine schedule with warmup, gradient clipping, early stop on **validation NASA score**, fully seeded. |
| **`at_ml.evaluate`** | RMSE, MAE, R², NASA asymmetric score, **per-horizon RMSE**, late-prediction rate, interval coverage. |
| **`at_ml.compare`** | Fair head-to-head harness — identical data, loss, optimiser, schedule and seed for every architecture — plus generated report. |
| **`at_ml.registry`** | TorchScript export with **verified fidelity**, JSON registry enforcing one PRODUCTION model per subset, auto-generated model cards. |

---

## (b) Results

### FD001 (100 units, single condition, HPC fault)

| model | params | RMSE | NASA score | R² | RMSE (RUL≤25) | train s |
|---|---:|---:|---:|---:|---:|---:|
| **mlp** | 173,569 | **12.54** | **221.9** | **0.902** | 4.54 | **9.4** |
| cnn | 30,401 | 14.94 | 308.8 | 0.861 | 4.48 | 31.4 |
| lstm | 344,002 | 14.21 | 299.1 | 0.874 | 4.50 | 118.7 |
| tcn | 131,266 | 15.11 | 302.5 | 0.858 | 6.00 | 157.0 |
| transformer | 160,353 | 15.08 | 509.2 | 0.858 | 3.95 | 273.4 |
| informer | 290,626 | 14.29 | 266.4 | 0.873 | **2.89** | 185.7 |

**Ensemble** (lstm + mlp + tcn, chosen on validation): RMSE **12.89**, score **223.5**.

### FD003 (100 units, HPC + Fan faults)

| model | RMSE | NASA score | R² |
|---|---:|---:|---:|
| **informer** | **13.14** | 296.9 | **0.888** |
| mlp | 13.46 | **235.9** | 0.882 |
| lstm | 14.29 | 340.1 | 0.867 |
| transformer | 15.07 | 703.6 | 0.852 |
| tcn | 16.40 | 469.7 | 0.825 |
| cnn | 16.60 | 473.2 | 0.820 |

### NFR-8 verification

| target | required | achieved |
|---|---|---|
| FD001 RMSE | ≤ 14.0 | **12.54** ✅ |
| FD001 NASA score | ≤ 350 | **221.9** ✅ |

Export divergence on both registered models: **0.0e+00** — TorchScript reproduces the trained model exactly.

---

## (c) The bug that mattered

The first run produced **validation RMSE 11.0** and **test RMSE 53.7**, with **R² −0.80** — worse than predicting the mean.

The cause was a leaking feature. `cycle_norm` was computed as `time_in_cycles / max(time_in_cycles)` per unit:

- In **training**, `max()` is the failure cycle → the feature encodes *fraction of life consumed*. The model learned `cycle_norm ≈ 1 ⇒ RUL ≈ 0`.
- In **test**, `max()` is merely where recording stopped → every test unit arrives at `cycle_norm ≈ 1` while having 100+ cycles left.

Every test engine was predicted as nearly dead.

Fixed by dividing by a fixed constant (400 cycles). Impact on FD001/TCN: **RMSE 53.7 → 15.1, R² −0.80 → 0.86, NASA score 22,528 → 302**.

I'm flagging this prominently because **the validation score looked excellent while the model was worthless** — which is exactly how this class of leak reaches a published result. Two regression tests now guard it: one asserts `cycle_norm` is invariant to trajectory length, the other scans every feature for >0.97 correlation with the target.

---

## (d) The uncomfortable result

**A flattened-window MLP beats every sequence model on FD001 headline RMSE**, training in 9 seconds versus the transformer's 273.

I'm reporting this rather than quietly promoting a Transformer. Why it happens:

1. **The window is short and pre-normalised** (W=30, per-regime z-scored) — much of the structure a sequence model would learn has already been handed to it.
2. **The dataset is small** — 14k windows from 80 engines. This is Risk R3 from Doc 00 landing exactly as predicted.
3. **The target is capped** — above 125 cycles every label is identical, so roughly half the signal rewards simple models.

But the ranking flips on the metric that matters operationally:

- **Informer has the best near-failure accuracy on FD001** — RMSE **2.89** for RUL ≤ 25 vs the MLP's 4.54. That's the band that triggers maintenance.
- **Informer wins FD003 outright** — the harder subset with two fault modes.
- **The Doc 07 §7.3.5 question has an answer**: ProbSparse attention acts as a mild regulariser on short sequences and beats the dense Transformer on *every* metric in *both* subsets (FD001 score 266 vs 509). The efficiency argument is irrelevant at W=30; the regularisation argument holds.

**Open question for M5:** the registry promotes on validation NASA score, which selected MLP (FD003) and LSTM (FD001). If near-failure accuracy is the true objective, Informer is the better production choice. Recorded rather than silently resolved.

---

## (e) Not done, and why

| Item | Reason |
|---|---|
| **FD002 / FD004 training** | 3–4× the data; ~90 min per subset on 2 CPU cores. Pipeline is subset-agnostic — `make train` runs them where compute allows. |
| **Multi-seed runs** | Every figure is seed 42. Seed variance is unmeasured, so small gaps between adjacent rows shouldn't be over-read. Stated in the report. |
| **Uncertainty calibration** | MC-dropout + conformal intervals are M5, alongside serving. |
| **Serving integration** | The twin engine still uses the trend-extrapolation placeholder; `model_trusted=False` keeps it out of the health index. M5 wires the real model in. |

---

## (f) Cumulative state

| Metric | M1 | M2 | M3 | M4 |
|---|---|---|---|---|
| Tests | 135 | 181 | 319 | **372** |
| Contracts | 5 | 8 | 10 | **11** |
| mypy strict | clean | clean | clean | clean (56 files) |

New contract: **training code never enters the serving path** — `at_ml` may not import `fastapi`, `at_api` or `at_twin`, keeping the research stack out of the inference container.

---

## (g) Next: M5 — Inference Integration + Anomaly + XAI

1. Inference service loading TorchScript from the registry, adaptive micro-batching.
2. Twin engine calls it with a circuit breaker; `model_trusted=True` so RUL finally drives the health index.
3. Uncertainty: MC-dropout + conformal intervals → real p10/p50/p90 on the dashboard.
4. Anomaly detection: residual EWMA + CUSUM + autoencoder, module attribution.
5. XAI: Integrated Gradients online, SHAP on demand.

This is the milestone where the dashboard's RUL column stops being a placeholder.
