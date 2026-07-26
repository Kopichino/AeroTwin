'use client';

/**
 * Fleet table (Doc 15 section 15.2.2).
 *
 * Rows are memoised on the fields that actually render, so a 4 Hz fleet delta
 * touching three engines re-renders three rows rather than all 260.
 */

import { memo, useMemo } from 'react';

import { HealthBar, HealthPill, bandColour } from '@/components/ui';
import type { FleetRow, HealthBand } from '@/lib/types';
import { type SortKey, selectFleetView, useFleetStore } from '@/stores/fleet-store';

function anomalyStyle(score: number): { color: string; background: string } {
  const colour =
    score >= 11 ? 'var(--health-crit)' : score >= 9 ? 'var(--health-warn)' : 'var(--anomaly)';
  return { color: colour, background: `color-mix(in srgb, ${colour} 12%, transparent)` };
}

const Row = memo(
  function Row({ row, onSelect }: { row: FleetRow; onSelect: (id: string) => void }) {
    return (
      <tr
        onClick={() => onSelect(row.engine_id)}
        className="cursor-pointer transition-colors duration-150 hover:bg-white/[0.03]"
      >
        <td className="px-3 py-2.5 font-mono text-xs">{row.tail_number ?? '—'}</td>
        <td className="tabular px-3 py-2.5 text-right text-xs text-tertiary">
          {row.unit_number}
        </td>
        <td className="tabular px-3 py-2.5 text-right text-xs">{row.cycle}</td>
        <td
          className="tabular px-3 py-2.5 text-right text-[13px] font-semibold"
          style={{ color: bandColour(row.health_band) }}
        >
          {row.health_index.toFixed(1)}
        </td>
        <td className="px-3 py-2.5">
          <HealthBar value={row.health_index} band={row.health_band} />
        </td>
        <td className="px-3 py-2.5">
          <HealthPill band={row.health_band} />
        </td>
        <td className="px-3 py-2.5 font-mono text-[11px] text-secondary">
          {row.worst_module ?? '—'}
        </td>
        <td className="tabular px-3 py-2.5 text-right text-xs text-secondary">
          {row.rul_p50 != null ? Math.round(row.rul_p50) : '—'}
          {row.rul_p50 != null && !row.model_backed ? (
            <span
              className="text-tertiary"
              title="Trend estimate — this engine has not accumulated enough history to be scored by the model yet"
            >
              *
            </span>
          ) : null}
        </td>
        <td className="tabular px-3 py-2.5 text-right font-mono text-[11px] text-tertiary">
          {row.rul_p10 != null && row.rul_p90 != null
            ? `${Math.round(row.rul_p10)}–${Math.round(row.rul_p90)}`
            : '—'}
        </td>
        <td className="px-3 py-2.5 text-right">
          {row.anomaly_alerting ? (
            <span
              className="tabular rounded-sm px-1.5 py-0.5 font-mono text-[11px]"
              style={anomalyStyle(row.anomaly_score)}
            >
              {row.anomaly_score.toFixed(1)}
            </span>
          ) : (
            <span className="tabular font-mono text-[11px] text-tertiary">
              {row.anomaly_score.toFixed(1)}
            </span>
          )}
        </td>
      </tr>
    );
  },
  (previous, next) =>
    previous.row.health_index === next.row.health_index &&
    previous.row.health_band === next.row.health_band &&
    previous.row.cycle === next.row.cycle &&
    previous.row.rul_p50 === next.row.rul_p50 &&
    previous.row.anomaly_score === next.row.anomaly_score &&
    previous.row.anomaly_alerting === next.row.anomaly_alerting &&
    previous.row.worst_module === next.row.worst_module &&
    previous.row.tail_number === next.row.tail_number,
);

const COLUMNS: { key: SortKey | null; label: string; align?: 'right' }[] = [
  { key: 'unit', label: 'Tail' },
  { key: 'unit', label: 'Unit', align: 'right' },
  { key: 'cycle', label: 'Cycle', align: 'right' },
  { key: 'health', label: 'Health', align: 'right' },
  { key: null, label: 'Condition' },
  { key: null, label: 'Band' },
  { key: null, label: 'Worst module' },
  { key: 'rul', label: 'RUL', align: 'right' },
  { key: null, label: '80% interval', align: 'right' },
  { key: 'anomaly', label: 'Anomaly', align: 'right' },
];

export function FleetGrid({ onSelect }: { onSelect: (id: string) => void }) {
  const state = useFleetStore();
  const view = useMemo(
    () => selectFleetView(state),
    // Recompute only when the data version or a control changes, not on every
    // frame: a delta that moves no ranked field must not re-sort 260 rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      state.version,
      state.sortKey,
      state.sortOrder,
      state.bandFilter,
      state.search,
      state.anomaliesOnly,
      state.engines,
    ],
  );

  const setSort = useFleetStore((s) => s.setSort);
  const sortKey = useFleetStore((s) => s.sortKey);
  const sortOrder = useFleetStore((s) => s.sortOrder);

  return (
    <div className="glass-panel overflow-hidden rounded-md">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <h2 className="text-[13px] font-semibold">Fleet</h2>
        <span className="ml-auto text-[11px] text-tertiary">
          {view.filtered === view.total
            ? `${view.total} engines`
            : `${view.filtered} of ${view.total} engines`}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <caption className="sr-only">
            Fleet of digital twin aircraft engines with health, remaining useful life
            and anomaly status
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((column, index) => {
                const sortable = column.key !== null;
                const active = sortable && sortKey === column.key;
                return (
                  <th
                    key={`${column.label}-${index}`}
                    scope="col"
                    aria-sort={
                      active ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined
                    }
                    className={`border-b border-line px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.07em] text-tertiary ${
                      column.align === 'right' ? 'text-right' : 'text-left'
                    }`}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => setSort(column.key as SortKey)}
                        className="uppercase tracking-[0.07em] transition-colors hover:text-secondary"
                      >
                        {column.label}
                        {active ? (sortOrder === 'asc' ? ' ↑' : ' ↓') : ''}
                      </button>
                    ) : (
                      column.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row) => (
              <Row key={row.engine_id} row={row} onSelect={onSelect} />
            ))}
          </tbody>
        </table>
      </div>

      {view.rows.length === 0 ? (
        <div className="px-6 py-12 text-center text-sm text-tertiary">
          No engines match the current filters.
        </div>
      ) : null}
    </div>
  );
}

export function BandFilterBar() {
  const summary = useFleetStore((s) => s.summary);
  const bandFilter = useFleetStore((s) => s.bandFilter);
  const setBandFilter = useFleetStore((s) => s.setBandFilter);
  const bands: HealthBand[] = ['HEALTHY', 'WATCH', 'WARNING', 'CRITICAL'];

  return (
    <div className="flex flex-wrap gap-2">
      {bands.map((band) => {
        const active = bandFilter === band;
        const colour = bandColour(band);
        const count = summary?.by_band?.[band] ?? 0;
        return (
          <button
            key={band}
            type="button"
            aria-pressed={active}
            onClick={() => setBandFilter(active ? null : band)}
            className={`glass-panel flex min-w-[130px] flex-1 items-center gap-2.5 rounded-md px-3.5 py-2.5 text-left transition-all duration-150 hover:-translate-y-px ${
              active ? 'ring-1 ring-accent' : ''
            }`}
          >
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: colour }} />
            <span className="text-xs text-secondary">{band[0] + band.slice(1).toLowerCase()}</span>
            <span className="tabular ml-auto font-mono text-base font-semibold" style={{ color: colour }}>
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
