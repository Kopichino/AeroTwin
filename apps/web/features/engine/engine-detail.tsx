'use client';

/**
 * Engine detail (Doc 15 section 15.2.3).
 *
 * Layout note: the left column is a **persistent canvas slot**. M8 replaces the
 * placeholder inside it with the React Three Fiber scene. Establishing the slot
 * now means the 3D work is a component swap rather than a page rewrite, and the
 * surrounding panels are already sized around it.
 */

import { useEffect, useMemo, useState } from 'react';

import { AttributionBars, LineChart, Sparkline, type Point } from '@/components/charts';
import { HealthBar, HealthPill, bandColour } from '@/components/ui';
import { CHARTED_SENSOR_META } from '@/lib/sensors';
import type { HistorySample } from '@/lib/types';
import { useFleetStore } from '@/stores/fleet-store';

type Tab = 'overview' | 'sensors' | 'prediction' | 'components';

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'sensors', label: 'Sensors' },
  { id: 'prediction', label: 'Prediction & XAI' },
  { id: 'components', label: 'Components' },
];

interface Explanation {
  available: boolean;
  reason?: string;
  rul_p50?: number;
  attributions?: { name: string; value: number; direction: string; module: string }[];
  module_scores?: Record<string, number>;
}

export function EngineDetail({ engineId }: { engineId: string }) {
  const twin = useFleetStore((state) => state.twins.get(engineId));
  const row = useFleetStore((state) => state.engines.get(engineId));

  const [history, setHistory] = useState<HistorySample[]>([]);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [error, setError] = useState<string | null>(null);

  // History is REST-backed rather than streamed: it is a bounded read that
  // changes slowly, and pushing 200 samples down the socket every tick would
  // dwarf the deltas it accompanies.
  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const response = await fetch(`/api/v1/engines/${engineId}/history?limit=200`);
        if (!response.ok) throw new Error(`history request failed (${response.status})`);
        const data = (await response.json()) as { samples: HistorySample[] };
        if (!cancelled) {
          setHistory(data.samples);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'unknown error');
      }
    }

    void load();
    const timer = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [engineId]);

  useEffect(() => {
    if (tab !== 'prediction') return;
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const response = await fetch(`/api/v1/engines/${engineId}/explain`);
        const data = (await response.json()) as Explanation;
        if (!cancelled) setExplanation(data);
      } catch {
        if (!cancelled) setExplanation({ available: false, reason: 'request failed' });
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [engineId, tab]);

  const healthSeries = useMemo<Point[]>(
    () => history.map((sample) => ({ x: sample.cycle, y: sample.health_index })),
    [history],
  );

  const rulSeries = useMemo<Point[]>(
    () =>
      history
        .filter((sample) => sample.rul_p50 != null)
        .map((sample) => ({ x: sample.cycle, y: sample.rul_p50 as number })),
    [history],
  );

  const rulBand = useMemo(() => {
    const rows = history.filter((s) => s.rul_p10 != null && s.rul_p90 != null);
    return {
      lower: rows.map((s) => ({ x: s.cycle, y: s.rul_p10 as number })),
      upper: rows.map((s) => ({ x: s.cycle, y: s.rul_p90 as number })),
    };
  }, [history]);

  const anomalySeries = useMemo<Point[]>(
    () => history.map((sample) => ({ x: sample.cycle, y: sample.anomaly_score })),
    [history],
  );

  const state = twin ?? null;
  const band = state?.health_band ?? row?.health_band ?? 'HEALTHY';
  const colour = bandColour(band);

  if (!state && !row) {
    return (
      <div className="glass-panel rounded-md px-6 py-16 text-center text-sm text-tertiary">
        Waiting for engine data…
      </div>
    );
  }

  const health = state?.health_index ?? row?.health_index ?? 0;
  const rul = state?.rul_p50 ?? row?.rul_p50 ?? null;
  const cycle = state?.cycle ?? row?.cycle ?? 0;

  return (
    <div className="space-y-4">
      <header className="glass-panel flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md px-4 py-3">
        <div>
          <h1 className="font-mono text-base">{state?.tail_number ?? row?.tail_number}</h1>
          <p className="text-[11px] text-tertiary">
            {state?.external_ref ?? `unit ${row?.unit_number}`} · cycle {cycle}
            {state ? ` / ${state.total_cycles}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <HealthPill band={band} />
          <span className="tabular font-mono text-lg font-semibold" style={{ color: colour }}>
            {health.toFixed(1)}
          </span>
          <HealthBar value={health} band={band} width={120} />
        </div>
        <div className="ml-auto flex items-center gap-6 text-xs">
          <div>
            <div className="text-tertiary">Remaining life</div>
            <div className="tabular font-mono text-sm">
              {rul != null ? `${Math.round(rul)} cycles` : '—'}
            </div>
          </div>
          <div>
            <div className="text-tertiary">80% interval</div>
            <div className="tabular font-mono text-sm text-secondary">
              {state?.rul_p10 != null && state?.rul_p90 != null
                ? `${Math.round(state.rul_p10)}–${Math.round(state.rul_p90)}`
                : '—'}
            </div>
          </div>
          <div>
            <div className="text-tertiary">Worst module</div>
            <div className="font-mono text-sm">{state?.worst_module ?? row?.worst_module ?? '—'}</div>
          </div>
        </div>
      </header>

      {error ? (
        <div
          role="alert"
          className="rounded-md border border-critical/40 bg-critical/10 px-4 py-2 text-xs text-critical"
        >
          Could not load history: {error}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        {/*
          Persistent canvas slot. M8 mounts the React Three Fiber turbofan here;
          until then the same space shows the component health readout so the
          layout is real rather than a reserved blank.
        */}
        <section aria-label="Engine visualisation" className="glass-panel rounded-md p-4">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-[13px] font-semibold">Engine</h2>
            <span className="ml-auto rounded-sm border border-line px-2 py-0.5 text-[10px] text-tertiary">
              3D view in M8
            </span>
          </div>
          <ComponentSchematic components={state?.components ?? {}} worst={state?.worst_module ?? null} />
        </section>

        <section className="glass-panel overflow-hidden rounded-md">
          <div role="tablist" aria-label="Engine detail sections" className="flex border-b border-line">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                role="tab"
                type="button"
                aria-selected={tab === entry.id}
                onClick={() => setTab(entry.id)}
                className={`px-4 py-2.5 text-xs transition-colors ${
                  tab === entry.id
                    ? 'border-b-2 border-accent text-primary'
                    : 'text-tertiary hover:text-secondary'
                }`}
              >
                {entry.label}
              </button>
            ))}
          </div>

          <div className="p-4">
            {tab === 'overview' ? (
              <div className="space-y-5">
                <ChartBlock title="Health index" hint="0–100, EWMA-smoothed">
                  <LineChart
                    series={healthSeries}
                    colour={colour}
                    label="Health index over engine cycles"
                    yDomain={[0, 100]}
                    yFormat={(value) => value.toFixed(0)}
                  />
                </ChartBlock>
                <ChartBlock title="Anomaly score" hint="fused detector output, in sigma">
                  <LineChart
                    series={anomalySeries}
                    colour="var(--anomaly)"
                    label="Anomaly score over engine cycles"
                    height={110}
                  />
                </ChartBlock>
              </div>
            ) : null}

            {tab === 'sensors' ? <SensorGrid history={history} sensors={state?.sensors ?? {}} /> : null}

            {tab === 'prediction' ? (
              <div className="space-y-5">
                <ChartBlock
                  title="Remaining useful life"
                  hint="shaded band is the conformal 80% interval"
                >
                  <LineChart
                    series={rulSeries}
                    band={rulBand}
                    colour="var(--accent)"
                    label="Predicted remaining useful life with 80 percent interval"
                    yFormat={(value) => value.toFixed(0)}
                  />
                </ChartBlock>

                <div>
                  <h3 className="mb-2 text-xs font-semibold">Why this prediction</h3>
                  {explanation === null ? (
                    <p className="text-xs text-tertiary">Computing attribution…</p>
                  ) : explanation.available ? (
                    <>
                      <AttributionBars items={explanation.attributions ?? []} />
                      <p className="mt-2 text-[11px] text-tertiary">
                        Sensor contributions to the current prediction, by gradient magnitude.
                        Arrows show the direction of influence.
                      </p>
                    </>
                  ) : (
                    <p className="text-xs text-tertiary">
                      Attribution unavailable — {explanation.reason}.
                    </p>
                  )}
                </div>

                {state?.failure_prob && Object.keys(state.failure_prob).length > 0 ? (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold">Failure probability</h3>
                    <div className="flex gap-4">
                      {Object.entries(state.failure_prob).map(([horizon, probability]) => (
                        <div key={horizon} className="glass-panel flex-1 rounded-sm px-3 py-2">
                          <div className="text-[10px] uppercase tracking-wide text-tertiary">
                            within {horizon} cycles
                          </div>
                          <div
                            className="tabular font-mono text-lg"
                            style={{
                              color:
                                probability > 0.5
                                  ? 'var(--health-crit)'
                                  : probability > 0.2
                                    ? 'var(--health-warn)'
                                    : 'var(--health-good)',
                            }}
                          >
                            {(probability * 100).toFixed(0)}%
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {tab === 'components' ? (
              <ComponentTable components={state?.components ?? {}} drivers={state?.drivers ?? []} />
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function ChartBlock({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <h3 className="text-xs font-semibold">{title}</h3>
        {hint ? <span className="text-[10px] text-tertiary">{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

function ComponentSchematic({
  components,
  worst,
}: {
  components: Record<string, number>;
  worst: string | null;
}) {
  const order = ['FAN', 'LPC', 'HPC', 'COMBUSTOR', 'HPT', 'LPT', 'NOZZLE'];
  const present = order.filter((module) => module in components);

  if (present.length === 0) {
    return <p className="text-xs text-tertiary">Component health not yet available.</p>;
  }

  return (
    <div className="space-y-2">
      {present.map((module) => {
        const score = components[module] ?? 0;
        const band = score >= 80 ? 'HEALTHY' : score >= 60 ? 'WATCH' : score >= 35 ? 'WARNING' : 'CRITICAL';
        const colour = bandColour(band);
        const isWorst = module === worst;
        return (
          <div
            key={module}
            className={`flex items-center gap-3 rounded-sm px-2 py-1.5 ${
              isWorst ? 'bg-white/[0.04] ring-1 ring-inset ring-white/10' : ''
            }`}
          >
            <span className="w-24 shrink-0 font-mono text-[11px]">{module}</span>
            <span className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
              <span
                className="absolute inset-y-0 left-0 rounded-full transition-[width,background-color] duration-500 ease-spring"
                style={{ width: `${Math.max(2, score)}%`, background: colour }}
              />
            </span>
            <span className="tabular w-10 text-right font-mono text-[11px]" style={{ color: colour }}>
              {score.toFixed(0)}
            </span>
          </div>
        );
      })}
      <p className="pt-1 text-[10px] text-tertiary">
        Derived from thermodynamic efficiency and flow-capacity proxies, each measured against
        this engine&apos;s own healthy baseline.
      </p>
    </div>
  );
}

function ComponentTable({
  components,
  drivers,
}: {
  components: Record<string, number>;
  drivers: string[];
}) {
  const rows = Object.entries(components).sort(([, a], [, b]) => a - b);

  if (rows.length === 0) {
    return <p className="text-xs text-tertiary">Component health not yet available.</p>;
  }

  return (
    <div className="space-y-4">
      <table className="w-full">
        <caption className="sr-only">Component health scores</caption>
        <thead>
          <tr>
            <th scope="col" className="pb-2 text-left text-[10px] uppercase tracking-wide text-tertiary">
              Module
            </th>
            <th scope="col" className="pb-2 text-left text-[10px] uppercase tracking-wide text-tertiary">
              Condition
            </th>
            <th scope="col" className="pb-2 text-right text-[10px] uppercase tracking-wide text-tertiary">
              Score
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([module, score]) => {
            const band = score >= 80 ? 'HEALTHY' : score >= 60 ? 'WATCH' : score >= 35 ? 'WARNING' : 'CRITICAL';
            return (
              <tr key={module} className="border-t border-white/[0.04]">
                <td className="py-2 font-mono text-xs">{module}</td>
                <td className="py-2">
                  <HealthBar value={score} band={band} width={140} />
                </td>
                <td
                  className="tabular py-2 text-right font-mono text-xs"
                  style={{ color: bandColour(band) }}
                >
                  {score.toFixed(1)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {drivers.length > 0 ? (
        <div>
          <h3 className="mb-1.5 text-xs font-semibold">Degradation drivers</h3>
          <ul className="flex flex-wrap gap-1.5">
            {drivers.map((driver) => (
              <li
                key={driver}
                className="rounded-sm border border-line bg-glass px-2 py-1 font-mono text-[10px] text-secondary"
              >
                {driver}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function SensorGrid({
  history,
  sensors,
}: {
  history: HistorySample[];
  sensors: Record<string, number>;
}) {
  const keys = Object.keys(CHARTED_SENSOR_META).filter((key) => key in sensors);

  if (keys.length === 0) {
    return <p className="text-xs text-tertiary">Sensor data not yet available.</p>;
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {keys.map((key) => {
        const meta = CHARTED_SENSOR_META[key];
        const values = history
          .map((sample) => sample.sensors[key])
          .filter((value): value is number => value != null);
        return (
          <div key={key} className="glass-panel rounded-sm p-3">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs">{meta?.symbol ?? key}</span>
              <span className="text-[10px] text-tertiary">{meta?.module}</span>
              <span className="tabular ml-auto font-mono text-sm">
                {(sensors[key] ?? 0).toFixed(1)}
              </span>
            </div>
            <p className="mt-0.5 truncate text-[10px] text-tertiary">{meta?.description}</p>
            <div className="mt-2">
              <Sparkline
                values={values}
                label={`${meta?.symbol ?? key} trend`}
                colour="var(--accent)"
                width={200}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
