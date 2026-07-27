'use client';

/**
 * Mission control (Doc 15 sections 15.2.1 and 15.2.2).
 *
 * A client component in full: every number on this page arrives over the
 * websocket, so there is nothing meaningful to render on the server. The shell
 * paints immediately and fills in as the first snapshot lands.
 */

import { useRouter } from 'next/navigation';
import { useCallback } from 'react';

import { ConnectionBadge, StatTile, bandColour } from '@/components/ui';
import { BandFilterBar, FleetGrid } from '@/features/fleet/fleet-grid';
import { useFleetStream } from '@/hooks/use-fleet-stream';
import { useFleetStore } from '@/stores/fleet-store';

export default function FleetPage() {
  useFleetStream();

  const connection = useFleetStore((s) => s.connection);
  const attempt = useFleetStore((s) => s.reconnectAttempt);
  const summary = useFleetStore((s) => s.summary);
  const system = useFleetStore((s) => s.system);
  const engines = useFleetStore((s) => s.engines);
  const search = useFleetStore((s) => s.search);
  const setSearch = useFleetStore((s) => s.setSearch);
  const anomaliesOnly = useFleetStore((s) => s.anomaliesOnly);
  const toggleAnomaliesOnly = useFleetStore((s) => s.toggleAnomaliesOnly);

  const router = useRouter();
  const handleSelect = useCallback(
    (id: string) => router.push(`/engines/${id}`),
    [router],
  );

  const rows = [...engines.values()];
  const alerting = rows.filter((row) => row.anomaly_alerting).length;
  const modelBacked = rows.filter((row) => row.model_backed).length;
  const health = summary?.avg_health ?? 0;
  const healthColour =
    health >= 80 ? bandColour('HEALTHY') : health >= 60 ? bandColour('WATCH') : bandColour('WARNING');

  return (
    <main className="mx-auto max-w-[1500px] px-6 py-6">
      <header className="mb-6 flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold tracking-tight">AeroTwin</h1>
        <ConnectionBadge status={connection} attempt={attempt} />
        {system ? (
          <>
            <span className="rounded-sm border border-line bg-glass px-2.5 py-1 font-mono text-[11px] text-secondary">
              {system.ticks} ticks · up {system.uptime_s}s
            </span>
            <span className="rounded-sm border border-line bg-glass px-2.5 py-1 font-mono text-[11px] text-secondary">
              tick p50 {system.tick_p50_ms}ms · p99 {system.tick_p99_ms}ms
            </span>
          </>
        ) : null}
      </header>

      <section
        aria-label="Fleet key performance indicators"
        className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(165px,1fr))] gap-3"
      >
        <StatTile
          label="Engines"
          value={summary?.engines ?? '—'}
          sub={`${summary?.active ?? 0} running`}
        />
        <StatTile
          label="Fleet health"
          value={summary ? health.toFixed(1) : '—'}
          sub={`virtual cycle ${summary?.cycle ?? 0}`}
          accent={healthColour}
        />
        <StatTile
          label="At risk"
          value={summary?.at_risk ?? '—'}
          sub="warning + critical"
          accent={summary?.at_risk ? bandColour('WARNING') : undefined}
        />
        <StatTile
          label="Anomalies"
          value={alerting}
          sub="engines alerting"
          accent={alerting > 0 ? 'var(--anomaly)' : undefined}
        />
        <StatTile
          label="Model coverage"
          value={rows.length ? `${modelBacked}/${rows.length}` : '—'}
          sub={
            system?.inference?.models_loaded
              ? `${system.inference.calls} calls · ${system.inference.latency_p50_ms}ms`
              : 'no model loaded'
          }
        />
      </section>

      <section aria-label="Filter by health band" className="mb-4">
        <BandFilterBar />
      </section>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="fleet-search">
          Search by tail number or unit
        </label>
        <input
          id="fleet-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search tail or unit…"
          className="w-56 rounded-md border border-line bg-glass px-3 py-2 text-sm placeholder:text-tertiary focus:border-accent focus:outline-none"
        />
        <button
          type="button"
          aria-pressed={anomaliesOnly}
          onClick={toggleAnomaliesOnly}
          className={`rounded-md border px-3 py-2 text-xs transition-colors ${
            anomaliesOnly
              ? 'border-accent text-accent'
              : 'border-line text-secondary hover:border-line-strong'
          }`}
        >
          Anomalies only
        </button>
      </div>

      <FleetGrid onSelect={handleSelect} />

      <footer className="mt-6 text-center text-[11px] text-tertiary">
        Live digital twins replaying NASA C-MAPSS · physics-informed component health ·
        deep RUL with conformal 80% intervals · <code>*</code> = trend estimate, no model
      </footer>
    </main>
  );
}
