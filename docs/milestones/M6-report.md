# M6 — Frontend Foundation & Fleet Dashboard — Completion Report

**Status:** complete, awaiting approval
**Commit:** `e94794a` · 423 backend + 29 frontend tests · 11 architecture contracts kept

---

## (a) What was built

| Component | Detail |
|---|---|
| **Stack** | Next.js 16 · React 19 · TypeScript strict (`noUncheckedIndexedAccess`) · Tailwind wired to the Doc 06 design tokens |
| **`lib/ws-client`** | Multiplexed socket, refcounted channels, **rAF frame batching**, exponential backoff with jitter, re-subscribe on every reconnect |
| **`stores/fleet-store`** | Zustand realtime state. Engines in a `Map` so a delta is O(changed); a **version counter** means a delta touching no ranked field never triggers a re-sort |
| **`features/fleet`** | Sortable table with per-row memoisation, band filter chips, search, anomalies-only toggle |
| **`app/fleet`** | Mission control: KPI tiles, connection badge, live tick metrics, detail panel |
| **Tests** | 29 (13 websocket, 16 store/selector) |
| **CI** | New `web` job: npm audit → typecheck → vitest → production build |

Run it: `make demo` in one terminal, `make web` in another → **http://localhost:3000/fleet**

---

## (b) Evidence

Verified against the **live backend**, not mocks:

| check | result |
|---|---|
| `tsc --noEmit` strict | clean |
| `next build` | 3 routes prerendered |
| Frontend tests | 29/29 |
| Backend gates | 423/423, 11 contracts |
| Page load | HTTP 200 in 375 ms |
| REST proxy through Next | 100 engines returned |
| WebSocket → snapshot | 100 engines, live deltas |
| Backend under UI load | tick p99 **68 ms**, 1,467 inference calls |

`docs/preview/fleet-ui-preview.html` is a static render of the real UI with live data: 100 engines, 77 alerting, 91 model-backed.

---

## (c) Three real problems found

**1. Next 15.1.6 shipped with a CVE.** npm flagged it during install. Upgraded to 16.2.12 — but `npm audit` then reported 3 high-severity transitive advisories in Next's bundled `postcss` and `sharp`, and `npm audit fix --force` "resolves" them by **downgrading Next to v9**. Pinned the patched versions through `overrides` instead. Now **0 vulnerabilities**, and CI runs `npm audit --audit-level=high` so a regression fails the build.

**2. Next `rewrites` do not proxy WebSocket upgrades.** I'd assumed one origin would cover both. A handshake through `:3000` **timed out** while the identical request to `:8000` succeeded. The client now addresses the API origin directly in development, and same-origin behind the reverse proxy in production. Documented at the call site so it isn't "fixed" back later.

**3. Store `reset()` left view filters applied.** Caught by three tests failing from leaked state. That's a real user-facing bug — a reset that silently keeps a CRITICAL filter hides most of the fleet.

Also: `pandas-stubs` had been installed ad-hoc in M4/M5 and was never added to the install target, so `make check` failed on a clean environment. Now pinned in both the Makefile and CI.

---

## (d) Honest gaps

- **No virtualization yet.** The grid caps at 200 rows. At 260 engines this is fine on the hardware I can test, but Doc 06 NFR-5 specifies TanStack Virtual and it is not in. Needed before the FD002/FD004 fleets.
- **No TanStack Query.** All data currently arrives over the websocket, so there is no REST-backed server state to cache yet. It lands with the typed OpenAPI client.
- **Types are hand-written.** Doc 12 §12.10 specifies generating them from the committed schema. The file is structured to be replaced wholesale rather than merged.
- **No Storybook, no Playwright.** Doc 16 lists both for M6. Neither fits the disk budget here alongside torch; both are CI-appropriate rather than sandbox-appropriate.
- **`/dashboard` still exists.** The old single-file HTML dashboard is still served by the API. It is useful as a zero-dependency fallback, but it now duplicates the fleet view and should be retired or explicitly reframed.

---

## (e) Environment note

The sandbox ran out of disk mid-milestone: `/tmp` is a 993 MB tmpfs and pip's torch build overflowed it. Fixed by pointing `TMPDIR` at the main volume, and a stray CUDA torch install (much larger than the CPU wheel) was replaced with `torch==2.13.0+cpu`. Worth recording because a clean `make install` on a constrained machine would hit the same wall.

---

## (f) Cumulative state

| Metric | M3 | M4 | M5 | M6 |
|---|---|---|---|---|
| Backend tests | 319 | 372 | 423 | 423 |
| Frontend tests | — | — | — | **29** |
| Contracts | 10 | 11 | 11 | 11 |
| mypy strict | clean | clean | clean | clean |

---

## (g) Next: M7 — Engine Detail + Charts

Per-engine route with sensor history, RUL timeline with the conformal band, component health tree, anomaly feed, and the XAI attribution panel. This is also where the persistent canvas slot goes in, so M8's 3D scene drops into a layout that already exists.
