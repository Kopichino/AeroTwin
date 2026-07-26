/**
 * Realtime state (Doc 14).
 *
 * Zustand holds anything arriving over the websocket; TanStack Query will hold
 * REST-backed server state when the typed client lands. The split matters
 * because fleet deltas arrive several times a second and must not invalidate a
 * query cache that backs paginated tables.
 *
 * Engines are stored in a `Map` so applying a delta is O(changed) rather than
 * O(fleet). Sorting and filtering are derived in a selector and memoised on a
 * version counter, so a delta that does not touch a ranked field costs nothing.
 */

import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';

import type {
  ConnectionStatus,
  FleetRow,
  FleetSummary,
  HealthBand,
  SystemStats,
  TwinDetail,
} from '@/lib/types';

export type SortKey = 'health' | 'rul' | 'cycle' | 'unit' | 'anomaly';
export type SortOrder = 'asc' | 'desc';

/** Bounded so a long-running tab cannot grow without limit (Doc 14 section 14.7). */
const MAX_OPEN_TWINS = 8;
const MAX_EVENTS = 100;

export interface TwinEvent {
  engine_id: string;
  seq: number;
  cycle: number;
  event_type: string;
  severity: string;
  payload: Record<string, unknown>;
  received_at: number;
}

interface FleetState {
  connection: ConnectionStatus;
  reconnectAttempt: number;
  framesReceived: number;

  engines: Map<string, FleetRow>;
  summary: FleetSummary | null;
  system: SystemStats | null;
  /** Bumped only when a delta changes a field the grid sorts or filters on. */
  version: number;

  twins: Map<string, TwinDetail>;
  events: TwinEvent[];

  sortKey: SortKey;
  sortOrder: SortOrder;
  bandFilter: HealthBand | null;
  search: string;
  anomaliesOnly: boolean;

  setConnection: (status: ConnectionStatus, attempt: number) => void;
  applyFleetSnapshot: (summary: FleetSummary) => void;
  applyTwinDelta: (detail: TwinDetail) => void;
  pushEvent: (event: TwinEvent) => void;
  setSystem: (stats: SystemStats) => void;
  setSort: (key: SortKey) => void;
  setBandFilter: (band: HealthBand | null) => void;
  setSearch: (value: string) => void;
  toggleAnomaliesOnly: () => void;
  reset: () => void;
}

/** Fields whose change requires the grid to re-sort or re-filter. */
function affectsRanking(previous: FleetRow | undefined, next: FleetRow): boolean {
  if (!previous) return true;
  return (
    previous.health_index !== next.health_index ||
    previous.health_band !== next.health_band ||
    previous.rul_p50 !== next.rul_p50 ||
    previous.anomaly_alerting !== next.anomaly_alerting ||
    previous.status !== next.status
  );
}

export const useFleetStore = create<FleetState>()(
  subscribeWithSelector((set) => ({
    connection: 'connecting',
    reconnectAttempt: 0,
    framesReceived: 0,

    engines: new Map(),
    summary: null,
    system: null,
    version: 0,

    twins: new Map(),
    events: [],

    sortKey: 'health',
    sortOrder: 'asc',
    bandFilter: null,
    search: '',
    anomaliesOnly: false,

    setConnection: (status, attempt) =>
      set({ connection: status, reconnectAttempt: attempt }),

    applyFleetSnapshot: (summary) =>
      set((state) => {
        const rows = summary.engines_list;
        if (!rows) return { summary, framesReceived: state.framesReceived + 1 };

        const engines = new Map(state.engines);
        let rankingChanged = false;
        for (const row of rows) {
          if (!rankingChanged && affectsRanking(engines.get(row.engine_id), row)) {
            rankingChanged = true;
          }
          engines.set(row.engine_id, row);
        }

        return {
          engines,
          summary,
          version: rankingChanged ? state.version + 1 : state.version,
          framesReceived: state.framesReceived + 1,
        };
      }),

    applyTwinDelta: (detail) =>
      set((state) => {
        const twins = new Map(state.twins);
        // Evict the oldest entry once the cap is reached: only a handful of
        // engines are ever open at once, and full detail payloads are large.
        if (!twins.has(detail.engine_id) && twins.size >= MAX_OPEN_TWINS) {
          const oldest = twins.keys().next().value;
          if (oldest) twins.delete(oldest);
        }
        twins.set(detail.engine_id, detail);
        return { twins, framesReceived: state.framesReceived + 1 };
      }),

    pushEvent: (event) =>
      set((state) => ({ events: [event, ...state.events].slice(0, MAX_EVENTS) })),

    setSystem: (system) => set({ system }),

    setSort: (key) =>
      set((state) => ({
        sortKey: key,
        // Clicking the active column flips direction; a new column starts in the
        // direction that puts the most urgent engines first.
        sortOrder:
          state.sortKey === key ? (state.sortOrder === 'asc' ? 'desc' : 'asc') : 'asc',
      })),

    setBandFilter: (band) => set({ bandFilter: band }),
    setSearch: (search) => set({ search }),
    toggleAnomaliesOnly: () => set((state) => ({ anomaliesOnly: !state.anomaliesOnly })),

    // Clears view controls as well as data. A reset that left a band filter or
    // search term applied would silently hide engines from the next session.
    reset: () =>
      set({
        engines: new Map(),
        twins: new Map(),
        events: [],
        summary: null,
        system: null,
        version: 0,
        sortKey: 'health',
        sortOrder: 'asc',
        bandFilter: null,
        search: '',
        anomaliesOnly: false,
      }),
  })),
);

/** Sort comparators. `null` RUL sorts last: unknown is not the same as urgent. */
const COMPARATORS: Record<SortKey, (a: FleetRow, b: FleetRow) => number> = {
  health: (a, b) => a.health_index - b.health_index,
  rul: (a, b) => (a.rul_p50 ?? Number.POSITIVE_INFINITY) - (b.rul_p50 ?? Number.POSITIVE_INFINITY),
  cycle: (a, b) => a.cycle - b.cycle,
  unit: (a, b) => a.unit_number - b.unit_number,
  anomaly: (a, b) => b.anomaly_score - a.anomaly_score,
};

export interface FleetView {
  rows: FleetRow[];
  total: number;
  filtered: number;
}

/**
 * Derive the visible rows. Pure and exported so it can be unit-tested without
 * mounting a component.
 */
export function selectFleetView(state: FleetState, limit = 200): FleetView {
  const all = [...state.engines.values()];
  let rows = all;

  if (state.bandFilter) rows = rows.filter((row) => row.health_band === state.bandFilter);
  if (state.anomaliesOnly) rows = rows.filter((row) => row.anomaly_alerting);

  const needle = state.search.trim().toLowerCase();
  if (needle) {
    rows = rows.filter(
      (row) =>
        (row.tail_number ?? '').toLowerCase().includes(needle) ||
        String(row.unit_number) === needle,
    );
  }

  const comparator = COMPARATORS[state.sortKey];
  rows = [...rows].sort(
    state.sortOrder === 'asc' ? comparator : (a, b) => comparator(b, a),
  );

  return { rows: rows.slice(0, limit), total: all.length, filtered: rows.length };
}
