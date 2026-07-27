---
doc_id: AT9000-AMM-72
title: AT-9000 Aircraft Maintenance Manual — Chapter 72, Engine
source_type: AMM
publisher: AeroTwin Industries (fictional)
revision: Rev 14
license: Authored for this project. Not a real manufacturer document.
ata_chapter: "72"
---

# Chapter 72 — Engine

> **Provenance.** This manual is written for the AeroTwin project. The AT-9000 is a
> fictional turbofan. Procedures, task codes and limits follow the structure and
> tone of real ATA-format documentation so that retrieval, citation and grounding
> can be exercised realistically, but **none of it is airworthiness data and it
> must never be used to maintain a real engine.**

## 72-00-00 — General

The AT-9000 is a two-spool, high-bypass turbofan. The low-pressure spool carries
the fan and the low-pressure compressor (LPC), driven by the low-pressure turbine
(LPT). The high-pressure spool carries the high-pressure compressor (HPC), driven
by the high-pressure turbine (HPT).

Gas path order, front to rear: Fan → LPC → HPC → Combustor → HPT → LPT → Nozzle.

Condition monitoring is continuous. The engine reports 21 gas-path parameters per
flight cycle. Trend analysis of these parameters is the primary means of detecting
gradual deterioration before it becomes a dispatch event.

## 72-00-10 — Condition monitoring parameters

| Parameter | Symbol | Station | Primary indication |
|---|---|---|---|
| Total temperature at LPC outlet | T24 | 24 | LPC efficiency |
| Total temperature at HPC outlet | T30 | 30 | HPC efficiency |
| Total temperature at LPT outlet | T50 | 50 | Turbine gas-path condition, EGT |
| Total pressure at HPC outlet | P30 | 30 | HPC pressure delivery |
| Static pressure at HPC outlet | Ps30 | 30 | HPC discharge condition |
| Physical fan speed | Nf | — | Fan spool speed |
| Physical core speed | Nc | — | Core spool speed |
| Fuel flow ratio | phi | — | Combustor and overall efficiency |
| Bypass ratio | BPR | — | Fan aerodynamic condition |
| Bleed enthalpy | htBleed | — | HPC bleed system |
| HPT coolant bleed | W31 | — | HPT seal and clearance condition |
| LPT coolant bleed | W32 | — | LPT seal and clearance condition |

## 72-00-20 — Trend interpretation

Deterioration is assessed as a **deviation from the individual engine's own
baseline**, established over the first 20 flight cycles after installation, and
evaluated **within operating regime**. Comparing a cruise-condition reading
against a takeoff baseline produces a deviation dominated by flight condition
rather than engine wear, and is not a valid assessment.

A rising T30 at constant corrected speed indicates that the HPC is imparting more
heat for the same pressure rise, which is the classic signature of compressor
efficiency loss.

A rising T50 (exhaust gas temperature) accompanies most forms of core
deterioration. It is **not** by itself an indication of turbine distress: HPC
efficiency loss raises EGT because the core must burn more fuel for the same
thrust. Attribute EGT rise to the turbine only when it is accompanied by coolant
bleed drift or an independent turbine indication.

Falling W31 or W32 at constant power indicates coolant bleed drift, associated
with seal wear and increasing blade tip clearance.

## 72-31-00 — High-pressure compressor

### 72-31-00-200-801 — HPC performance trend check

**Interval:** every 50 flight cycles, or on trend alert.
**Skill:** powerplant technician.
**Duration:** 0.5 hours.

1. Retrieve the last 50 cycles of T24, T30, P30 and Ps30 for the affected engine.
2. Normalise each parameter within its operating regime.
3. Compute the deviation of T30/T24 from the engine's installation baseline.
4. If the ratio has risen more than **1.0 %**, raise a trend alert and proceed to
   task 72-31-00-200-802.
5. If the ratio has risen more than **2.0 %**, the engine is outside the
   serviceable performance band. Schedule performance restoration before the next
   scheduled check.

### 72-31-00-200-802 — HPC borescope inspection

**Interval:** on trend alert, or every 3,000 flight cycles.
**Skill:** borescope-qualified technician.
**Duration:** 4.0 hours. **Engine access:** required, engine off wing not required.

1. Ensure the engine has been shut down for a minimum of 2 hours and the core has
   cooled below 60 °C.
2. Remove borescope port plugs at stations 3 through 9.
3. Inspect each compressor stage for blade tip rub, leading-edge erosion,
   foreign object damage and coating loss.
4. Record findings against the damage limits in 72-31-00-200-803.
5. Reinstall port plugs to 12 Nm and perform a leak check.

**Findings that require immediate action:**

- Blade tip rub exceeding 0.5 mm depth on any stage.
- Leading-edge erosion exceeding 1.5 mm chord loss on more than three blades.
- Any crack of any length in a blade root or platform.

### 72-31-00-700-804 — HPC performance restoration (water wash)

**Interval:** on trend alert where borescope shows no mechanical damage.
**Duration:** 2.5 hours. **Effectiveness:** typically recovers 40–60 % of lost
efficiency where deterioration is fouling-dominated.

1. Confirm borescope inspection 72-31-00-200-802 shows no findings requiring
   immediate action.
2. Motor the engine on the starter with the fuel shutoff closed.
3. Inject demineralised water at 30 litres per minute for 60 seconds through the
   wash manifold.
4. Allow a 10-minute soak, then repeat the injection.
5. Dry-motor the engine for 90 seconds.
6. Perform a ground run and repeat task 72-31-00-200-801 to confirm recovery.

Water wash does not recover efficiency lost to blade erosion or tip clearance
increase. If the trend check after washing shows less than 20 % recovery, the
deterioration is mechanical and performance restoration by overhaul is indicated.

### 72-31-00-900-805 — HPC module replacement

**Duration:** 36 hours. **Engine access:** engine off wing required.
**Parts:** HPC module assembly P/N AT9-HPC-2200, seal kit P/N AT9-SK-118.

Required when HPC efficiency deviation exceeds 2.5 % and water wash has not
recovered performance, or when borescope findings exceed damage limits.

## 72-41-00 — Combustor

### 72-41-00-200-810 — Combustor fuel ratio trend check

**Interval:** every 100 flight cycles.

A rising fuel flow ratio (phi) at constant thrust indicates reduced combustion
efficiency, most commonly caused by fuel nozzle coking or liner distress.

1. Compare phi against the installation baseline within operating regime.
2. A rise exceeding 0.8 % indicates nozzle fouling. Schedule nozzle cleaning.
3. A rise exceeding 1.5 %, or any evidence of hot streaking in the turbine
   inspection, indicates liner distress. Perform combustor borescope inspection.

## 72-51-00 — High-pressure turbine

### 72-51-00-200-820 — HPT coolant bleed trend check

**Interval:** every 50 flight cycles.

Falling W31 at constant power indicates increasing leakage past the HPT seals,
which raises blade metal temperature and accelerates creep.

1. Compare W31 against the installation baseline within operating regime.
2. A fall exceeding 1.0 % indicates seal wear. Increase monitoring frequency.
3. A fall exceeding 2.0 %, particularly with a coincident T50 rise, indicates
   significant clearance increase. Schedule HPT inspection at the next
   opportunity.

### 72-51-00-200-821 — HPT blade inspection

**Duration:** 6.0 hours. **Engine access:** engine off wing required.

Inspect first-stage HPT blades for thermal barrier coating loss, creep elongation
and cooling hole blockage. Blade creep exceeding 0.8 % elongation requires blade
set replacement.

## 72-61-00 — Fan

### 72-61-00-200-830 — Fan condition check

**Interval:** every 200 flight cycles, and after any suspected bird strike.

Fan deterioration presents as a change in bypass ratio and a divergence between
physical and corrected fan speed at constant commanded thrust.

1. Inspect fan blades visually for leading-edge damage, nicks and dents.
2. Dress damage within the limits of 72-61-00-200-831.
3. Where BPR has fallen more than 1.2 % from baseline with no visible damage,
   perform fan trim balance.

## 72-00-90 — Dispatch and limits

| Condition | Action |
|---|---|
| HPC efficiency deviation < 1.0 % | Continue in service, normal monitoring |
| HPC efficiency deviation 1.0–2.0 % | Trend alert, borescope at next opportunity |
| HPC efficiency deviation > 2.0 % | Performance restoration before next check |
| HPC efficiency deviation > 2.5 % after wash | Module replacement |
| Predicted remaining useful life < 30 cycles | Plan removal, do not extend |
| Predicted remaining useful life < 10 cycles | Ground the engine, no further dispatch |
| Any crack in a rotating component | Ground the engine immediately |

Predicted remaining useful life produced by condition-monitoring software is
**decision support only**. It is not an airworthiness determination. Removal and
dispatch decisions remain the responsibility of the certifying engineer.
