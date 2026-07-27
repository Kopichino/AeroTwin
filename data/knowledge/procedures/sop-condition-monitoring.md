---
doc_id: SOP-CM-001
title: Standard Operating Procedure — Engine Condition Monitoring
source_type: SOP
publisher: AeroTwin Airlines (fictional)
revision: Rev 6
license: Authored for this project.
---

# SOP-CM-001 — Engine Condition Monitoring

## 1. Purpose

Defines how the reliability engineering team reviews engine condition data,
escalates deterioration, and converts a trend alert into a scheduled maintenance
action.

## 2. Daily review

The duty reliability engineer reviews the fleet dashboard at the start of each
shift and records:

- Every engine in WARNING or CRITICAL health band.
- Every engine with an open anomaly alert.
- Every engine whose predicted remaining useful life has fallen below 50 cycles.

## 3. Health bands

| Band | Health index | Meaning | Action |
|---|---|---|---|
| HEALTHY | 80–100 | Normal deterioration for age | Routine monitoring |
| WATCH | 60–79 | Deterioration above fleet median | Review trend at next daily check |
| WARNING | 35–59 | Significant deterioration | Raise a work package within 30 cycles |
| CRITICAL | below 35 | Approaching functional failure | Ground before next dispatch |

A band change only counts once it has persisted for three consecutive cycles.
Single-cycle excursions are sensor noise and must not trigger an action.

## 4. Anomaly response

An anomaly alert identifies the module whose sensor deviations are largest. It
does not, by itself, establish a root cause.

1. Confirm the alert has persisted for at least five cycles.
2. Identify the attributed module and the contributing sensors.
3. Cross-check against the relevant AMM trend task for that module.
4. If the trend task confirms deterioration, raise a work package.
5. If the trend task does not confirm it, treat the alert as a candidate sensor
   fault and raise an instrumentation check instead.

## 5. Escalation

| Trigger | Escalation |
|---|---|
| CRITICAL band | Notify fleet operations immediately, ground the engine |
| RUL below 30 cycles | Plan removal, notify maintenance planning |
| RUL below 10 cycles | Ground the engine, no further dispatch |
| Anomaly with HIGH or CRITICAL severity | Same-shift review by senior engineer |
| Three or more engines of the same subtype alerting | Raise a fleet-wide investigation |

## 6. Use of predictive model output

Remaining useful life predictions carry an 80 % confidence interval. Planning
decisions use the **lower bound**, not the median: planning against the median
means being wrong half the time, and half of those errors leave an aircraft
stranded.

Model output is advisory. Every work package derived from it requires review and
approval by a certifying engineer before execution.
