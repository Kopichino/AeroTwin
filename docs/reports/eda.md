# C-MAPSS Exploratory Data Analysis

_Generated 2026-07-26 10:25 UTC by `at_data.eda`. Regenerate with `make eda`._

Every figure below is computed from the official NASA files obtained by `make data`. Where a number contradicts a claim made during architecture planning, the contradiction is called out explicitly.

## 1. Dataset shape

| subset | train rows | test rows | train units | test units | conditions | fault modes |
|---|---|---|---|---|---|---|
| FD001 | 20,631 | 13,096 | 100 | 100 | 1 | 1 |
| FD002 | 53,759 | 33,991 | 260 | 259 | 6 | 1 |
| FD003 | 24,720 | 16,596 | 100 | 100 | 1 | 2 |
| FD004 | 61,249 | 41,214 | 249 | 248 | 6 | 2 |


**Total: 265,256 telemetry rows across 1,416 trajectories.**


## 2. Trajectory lengths and the window constraint

The sliding-window length is bounded by the *shortest test trajectory*: a window longer than it cannot be scored without padding.

| subset | train min | train median | train max | test min | window (ADR-013) | fits |
|---|---|---|---|---|---|---|
| FD001 | 128 | 199 | 362 | 31 | 30 | yes |
| FD002 | 128 | 199 | 378 | 21 | 20 | yes |
| FD003 | 145 | 220 | 525 | 38 | 30 | yes |
| FD004 | 128 | 234 | 543 | 19 | 18 | yes |


> **Planning correction.** ADR-013 originally specified a window of 20 for both FD002 and FD004, on the common assumption that the shortest test trajectory is 21 cycles. Measurement shows FD004's shortest is **19** (two units), which would have made those units unscoreable. The ADR was amended to use 18 for FD004.


## 3. Operating-condition regimes

| subset | regimes | silhouette | verdict |
|---|---|---|---|
| FD001 | 1 | 1.0000 | cleanly separated |
| FD002 | 6 | 0.9997 | cleanly separated |
| FD003 | 1 | 1.0000 | cleanly separated |
| FD004 | 6 | 0.9997 | cleanly separated |


**FD002 recovered flight conditions:**

| regime | altitude (kft) | Mach | TRA | rows |
|---|---|---|---|---|
| 0 | 0.002 | 0.000 | 100 | 8,044 |
| 1 | 10.003 | 0.250 | 100 | 8,096 |
| 2 | 20.003 | 0.701 | 100 | 8,122 |
| 3 | 25.003 | 0.621 | 60 | 8,002 |
| 4 | 35.003 | 0.841 | 100 | 8,037 |
| 5 | 42.003 | 0.840 | 100 | 13,458 |


The centroids land on physically meaningful conditions from sea level to 42,000 ft, which is strong evidence the clustering recovered the true generating conditions rather than arbitrary partitions. Doc 07's claim of silhouette > 0.95 is confirmed at **0.9997**.


## 4. Why per-regime normalisation is mandatory (ADR-014)

Taking T30 (s3, HPC outlet temperature) in FD002:

| measure | value |
|---|---|
| \|corr(raw s3, RUL)\| | **0.0324** |
| \|corr(per-regime z-scored s3, RUL)\| | **0.6180** |
| improvement | **19.1x** |
| mean within-regime std | 5.69 |
| between-regime spread of means | 119.72 |

The operating condition accounts for roughly 21 times more variance than the degradation signal. Without per-regime normalisation the degradation is effectively invisible to a model.


## 5. Sensor signal analysis and feature selection


### FD001 (14 sensors retained)

| sensor | symbol | module | std | n_unique | corr_raw | corr_regime_z | used |
|---|---|---|---|---|---|---|---|
| s1 | T2 | FAN | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s2 | T24 | LPC | 0.5001 | 310 | 0.6785 | 0.6785 | yes |
| s3 | T30 | HPC | 6.1311 | 3,012 | 0.6550 | 0.6550 | yes |
| s4 | T50 | LPT | 9.0006 | 4,051 | 0.7572 | 0.7572 | yes |
| s5 | P2 | FAN | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s6 | P15 | FAN | 0.0014 | 2 | 0.1083 | 0.1083 | - |
| s7 | P30 | HPC | 0.8851 | 513 | 0.7330 | 0.7330 | yes |
| s8 | Nf | FAN | 0.0710 | 53 | 0.6245 | 0.6245 | yes |
| s9 | Nc | HPC | 22.0829 | 6,403 | 0.4622 | 0.4622 | yes |
| s10 | epr | NOZZLE | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s11 | Ps30 | HPC | 0.2671 | 159 | 0.7752 | 0.7752 | yes |
| s12 | phi | COMBUSTOR | 0.7376 | 427 | 0.7489 | 0.7489 | yes |
| s13 | NRf | FAN | 0.0719 | 56 | 0.6240 | 0.6240 | yes |
| s14 | NRc | HPC | 19.0762 | 6,078 | 0.3698 | 0.3698 | yes |
| s15 | BPR | FAN | 0.0375 | 1,918 | 0.7209 | 0.7209 | yes |
| s16 | farB | COMBUSTOR | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s17 | htBleed | HPC | 1.5488 | 13 | 0.6808 | 0.6808 | yes |
| s18 | Nf_dmd | CONTROL | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s19 | PCNfR_dmd | CONTROL | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s20 | W31 | HPT | 0.1807 | 120 | 0.7046 | 0.7046 | yes |
| s21 | W32 | LPT | 0.1083 | 4,745 | 0.7073 | 0.7073 | yes |


Excluded: `s1`, `s5`, `s6`, `s10`, `s16`, `s18`, `s19`


### FD003 (15 sensors retained)

| sensor | symbol | module | std | n_unique | corr_raw | corr_regime_z | used |
|---|---|---|---|---|---|---|---|
| s1 | T2 | FAN | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s2 | T24 | LPC | 0.5230 | 334 | 0.6817 | 0.6817 | yes |
| s3 | T30 | HPC | 6.8104 | 3,358 | 0.6964 | 0.6964 | yes |
| s4 | T50 | LPT | 9.7732 | 4,383 | 0.7701 | 0.7701 | yes |
| s5 | P2 | FAN | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s6 | P15 | FAN | 0.0181 | 17 | 0.0964 | 0.0964 | - |
| s7 | P30 | HPC | 3.4373 | 1,854 | 0.3567 | 0.3567 | yes |
| s8 | Nf | FAN | 0.1583 | 161 | 0.6341 | 0.6341 | yes |
| s9 | Nc | HPC | 19.9803 | 7,114 | 0.6438 | 0.6438 | yes |
| s10 | epr | NOZZLE | 0.0035 | 4 | 0.4948 | 0.4948 | yes |
| s11 | Ps30 | HPC | 0.3001 | 170 | 0.8045 | 0.8045 | yes |
| s12 | phi | COMBUSTOR | 3.2553 | 1,772 | 0.3750 | 0.3750 | yes |
| s13 | NRf | FAN | 0.1581 | 163 | 0.6345 | 0.6345 | yes |
| s14 | NRc | HPC | 16.5041 | 6,320 | 0.5636 | 0.5636 | yes |
| s15 | BPR | FAN | 0.0605 | 3,122 | 0.0021 | 0.0021 | yes |
| s16 | farB | COMBUSTOR | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s17 | htBleed | HPC | 1.7615 | 12 | 0.7258 | 0.7258 | yes |
| s18 | Nf_dmd | CONTROL | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s19 | PCNfR_dmd | CONTROL | 0.0000 | 1 | 0.0000 | 0.0000 | - |
| s20 | W31 | HPT | 0.2489 | 165 | 0.0503 | 0.0503 | yes |
| s21 | W32 | LPT | 0.1492 | 6,440 | 0.0416 | 0.0416 | yes |


Excluded: `s1`, `s5`, `s6`, `s16`, `s18`, `s19`


> **Planning correction.** The literature commonly quotes a single list of seven constant sensors for all single-condition subsets. Direct measurement shows this is wrong in two ways:
>
> 1. **`s10` (epr)** is constant in FD001 but takes 4 distinct values in FD003 with |corr(RUL)| = 0.49. Dropping it discards real degradation signal, so FD003 retains 15 sensors while FD001 retains 14.
> 2. **`s6` (P15)** is *near*-constant rather than constant. It is excluded on a signal basis (|corr| < 0.15, few distinct values), not a variance basis.
>
> `at_core.domain.sensors` now encodes measured per-subset sets rather than a single hardcoded list.


## 6. Degradation behaviour

Sensors most predictive of remaining life in FD001:

| sensor | symbol | module | corr_regime_z |
|---|---|---|---|
| s11 | Ps30 | HPC | 0.7752 |
| s4 | T50 | LPT | 0.7572 |
| s12 | phi | COMBUSTOR | 0.7489 |
| s7 | P30 | HPC | 0.7330 |
| s15 | BPR | FAN | 0.7209 |
| s21 | W32 | LPT | 0.7073 |


Mean shift from healthy (RUL > 100) to near-failure (RUL < 20):

| sensor | symbol | healthy mean | near-failure mean | shift (sigma) |
|---|---|---|---|---|
| s3 | T30 | 1587.491 | 1600.046 | 2.691 |
| s4 | T50 | 1403.749 | 1424.947 | 3.529 |
| s11 | Ps30 | 47.383 | 48.031 | 3.710 |
| s12 | phi | 521.833 | 520.114 | -3.431 |
| s20 | W31 | 38.914 | 38.518 | -3.019 |
| s21 | W32 | 23.348 | 23.109 | -3.037 |


The signs match turbofan physics: HPC outlet temperature and pressure rise as compressor efficiency falls, while coolant bleed flows drift downward. This is the empirical basis for the efficiency proxies in Doc 08 section 8.4.


## 7. Implications for the platform

1. **Windows are subset-specific** (30 / 20 / 30 / 18), enforced by a test against the measured minimum test-trajectory length.
2. **Normalisation must be per regime**, with centroids fitted once and persisted so the twin engine classifies live telemetry identically.
3. **Feature sets are subset-specific** (14 / 21 / 15 / 21 sensors).
4. **RUL labels are capped at 125** (ADR-012); the uncapped label would otherwise ask the model to distinguish a 300-cycle-remaining engine from a 200-cycle one on indistinguishable sensor data.
5. **Test trajectories never reach failure**, so evaluation uses the final window per unit against the official label file.
