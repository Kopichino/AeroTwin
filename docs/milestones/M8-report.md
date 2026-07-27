# M8 — 3D Digital Twin Visualisation — Completion Report

**Status:** complete, awaiting approval
**Commit:** `f1be070` · 453 backend + 51 frontend tests · 11 architecture contracts kept

---

## (a) What was built

| Component | Detail |
|---|---|
| **`three/turbofan`** | Procedural geometry — seven meshes matching `EngineModule` exactly. Continuous health colour ramp, severity-driven emissive and roughness, CRITICAL-only pulse, fan rotating at physical fan speed, anomaly hotspots. |
| **`three/engine-scene`** | Canvas, three-point lighting, orbit controls, exploded (`E`) and X-ray (`X`) views, WebGL detection, SVG fallback, keyboard-accessible module listbox. |
| **Integration** | Dropped into the canvas slot M7 established — a component swap, no page rewrite. |
| **`tools/preview`** | Reusable static-preview renderer mirroring the scene's layout and colour ramp. |

---

## (b) Evidence — verified against a running production build

| check | result |
|---|---|
| Fleet page | HTTP 200, **19,999 B**, 4 ms |
| Detail page | HTTP 200, 7,849 B, 80 ms |
| **`three` on fleet page** | **no** — isolated in its own 890 KB chunk |
| **`three` on detail page initial chunks** | **no** — lazy-loaded on mount |
| Twin state feeding geometry | 7 modules, fan 2388.25 rpm, worst HPC, anomaly HPC |
| Tests | 453 backend, **51 frontend** (22 new), mypy clean |
| Dependency audit | 0 vulnerabilities |

Preview: `docs/preview/engine-3d-preview.html` — **AT-0092, HI 19.4, CRITICAL, HPC worst with active HPC anomaly.**

---

## (c) A real geometry bug the tests caught

I wrote module centres by hand and tuned half-lengths separately. They drifted apart: **five pairs of modules intersected**, by up to 0.35 units.

```
FAN       ends -2.76  ->  LPC   starts -2.90   overlap 0.14
LPC       ends -1.40  ->  HPC   starts -1.75   overlap 0.35
HPC       ends  0.25  ->  COMB  starts  0.00   overlap 0.25
HPT       ends  1.90  ->  LPT   starts  1.75   overlap 0.15
LPT       ends  2.95  ->  NOZ   starts  2.80   overlap 0.15
```

That z-fights wherever two translucent surfaces overlap — a flickering artefact that's obvious in motion and invisible in a screenshot. Positions are now **derived from lengths** rather than hand-placed, with a test asserting no overlap.

I'd have shipped this without the test. Asserting on geometry invariants rather than pixels is what made it catchable.

---

## (d) Three decisions worth flagging

**1. Procedural geometry, not a GLB.** A modelled asset would look better, but it's a multi-megabyte binary with no provenance in the repo, and it needs a named-mesh convention that silently breaks on re-export. Generated geometry keeps the module→mesh mapping explicit in code and reviewable in a diff. If the project later acquires a properly licensed turbofan model, the loader swaps in behind the same `MODULE_LAYOUT`.

**2. Continuous colour ramp, not four discrete colours.** An engine drifting 81 → 79 should look *slightly* worse, not change category. Discrete bands remain in the text labels, where the threshold is the point.

**3. Two fallbacks that matter as much as the 3D.** WebGL is unavailable in some corporate browsers, on VMs, and whenever a GPU driver misbehaves — a blank box would make the product look broken. And a raycast into a canvas is unreachable by keyboard and invisible to a screen reader, so modules are also selectable from a listbox.

---

## (e) Honest gaps

- **Camera does not fly to a selected module.** Doc 06 §6.7 specifies a spring transition to a preset view on click. Selection currently highlights in place.
- **No postprocessing.** Bloom and SMAA are specified; both cost frames on integrated GPUs and I could not measure real FPS here.
- **FPS is unmeasured.** NFR-4 requires ≥ 55 FPS at 1080p on an integrated GPU. This sandbox is headless — there is no GPU and no browser to measure in. **This NFR is unverified and I am not claiming it.**
- **Fan speed range is FD001-specific.** `2387.9–2388.6` is hardcoded; FD002/FD004 span different ranges and would need per-subset normalisation.
- **No exploded-view labels or leader lines**, which would make the exploded state considerably more readable.
- **Still no fleet virtualization** (carried from M6/M7).

---

## (f) Cumulative state

| Metric | M5 | M6 | M7 | M8 |
|---|---|---|---|---|
| Backend tests | 423 | 423 | 453 | 453 |
| Frontend tests | — | 29 | 29 | **51** |
| Contracts | 11 | 11 | 11 | 11 |
| Routes | 7 | 8 | 10 | 10 |

---

## (g) Next: M9 — RAG Knowledge Base

Corpus assembly (NASA TM/CR, FAA ACs, an authored AT-9000 AMM), structure-aware chunking, embeddings, hybrid retrieval with reranking, and the `/knowledge` search UI.

**Risk to flag now:** `sentence-transformers` pulls a large model download and this sandbox has hit its disk ceiling twice. I'll check the budget before committing, and if embeddings are not feasible here I'll say so rather than shipping an untested index — the retrieval layer is meaningless without one.
