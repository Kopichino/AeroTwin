import { beforeEach, describe, expect, it } from 'vitest';

import type { FleetRow, FleetSummary, TwinDetail } from '@/lib/types';
import { selectFleetView, useFleetStore } from '@/stores/fleet-store';

function row(overrides: Partial<FleetRow> = {}): FleetRow {
  return {
    engine_id: overrides.engine_id ?? 'e1',
    tail_number: 'AT-0001',
    unit_number: 1,
    status: 'RUNNING',
    cycle: 100,
    health_index: 80,
    health_band: 'HEALTHY',
    worst_module: 'HPC',
    rul_p50: 90,
    rul_p10: 70,
    rul_p90: 110,
    model_backed: true,
    anomaly_score: 1,
    anomaly_alerting: false,
    ...overrides,
  };
}

function summary(rows: FleetRow[]): FleetSummary {
  return {
    engines: rows.length,
    active: rows.length,
    failed: 0,
    avg_health: rows.reduce((sum, r) => sum + r.health_index, 0) / (rows.length || 1),
    by_band: { HEALTHY: 0, WATCH: 0, WARNING: 0, CRITICAL: 0 },
    at_risk: 0,
    speed: 8,
    engines_list: rows,
  };
}

describe('fleet store', () => {
  beforeEach(() => useFleetStore.getState().reset());

  it('applies a snapshot into the engine map', () => {
    useFleetStore.getState().applyFleetSnapshot(summary([row(), row({ engine_id: 'e2' })]));
    expect(useFleetStore.getState().engines.size).toBe(2);
  });

  it('updates existing engines in place rather than duplicating them', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary([row({ health_index: 80 })]));
    store.applyFleetSnapshot(summary([row({ health_index: 60 })]));

    const state = useFleetStore.getState();
    expect(state.engines.size).toBe(1);
    expect(state.engines.get('e1')?.health_index).toBe(60);
  });

  it('bumps the version only when a ranked field changes', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary([row()]));
    const afterFirst = useFleetStore.getState().version;

    // Cycle is not a ranked field, so the grid must not be told to re-sort.
    store.applyFleetSnapshot(summary([row({ cycle: 101 })]));
    expect(useFleetStore.getState().version).toBe(afterFirst);

    store.applyFleetSnapshot(summary([row({ health_index: 40 })]));
    expect(useFleetStore.getState().version).toBeGreaterThan(afterFirst);
  });

  it('handles a summary with no engine list', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot({ ...summary([]), engines_list: undefined });
    expect(useFleetStore.getState().summary).not.toBeNull();
  });

  it('caps open twin details so a long session cannot grow unbounded', () => {
    const store = useFleetStore.getState();
    for (let index = 0; index < 12; index += 1) {
      store.applyTwinDelta({ engine_id: `e${index}`, cycle: index } as TwinDetail);
    }
    expect(useFleetStore.getState().twins.size).toBeLessThanOrEqual(8);
  });

  it('caps the event buffer and keeps the newest first', () => {
    const store = useFleetStore.getState();
    for (let index = 0; index < 150; index += 1) {
      store.pushEvent({
        engine_id: 'e1',
        seq: index,
        cycle: index,
        event_type: 'twin.health.band_changed',
        severity: 'INFO',
        payload: {},
        received_at: index,
      });
    }
    const events = useFleetStore.getState().events;
    expect(events).toHaveLength(100);
    expect(events[0]?.seq).toBe(149);
  });

  it('toggles sort direction when the same column is clicked twice', () => {
    const store = useFleetStore.getState();
    store.setSort('health');
    expect(useFleetStore.getState().sortOrder).toBe('desc');
    useFleetStore.getState().setSort('health');
    expect(useFleetStore.getState().sortOrder).toBe('asc');
  });

  it('resets direction when a different column is chosen', () => {
    const store = useFleetStore.getState();
    store.setSort('health');
    useFleetStore.getState().setSort('rul');
    expect(useFleetStore.getState().sortOrder).toBe('asc');
  });
});

describe('selectFleetView', () => {
  beforeEach(() => useFleetStore.getState().reset());

  const fleet = [
    row({ engine_id: 'a', health_index: 90, rul_p50: 120, unit_number: 1, tail_number: 'AT-0001' }),
    row({
      engine_id: 'b',
      health_index: 30,
      health_band: 'CRITICAL',
      rul_p50: 10,
      unit_number: 2,
      tail_number: 'AT-0002',
      anomaly_alerting: true,
      anomaly_score: 12,
    }),
    row({
      engine_id: 'c',
      health_index: 60,
      health_band: 'WATCH',
      rul_p50: null,
      unit_number: 3,
      tail_number: 'AT-0003',
    }),
  ];

  it('sorts worst health first by default', () => {
    useFleetStore.getState().applyFleetSnapshot(summary(fleet));
    const view = selectFleetView(useFleetStore.getState());
    expect(view.rows.map((r) => r.engine_id)).toEqual(['b', 'c', 'a']);
  });

  it('sorts engines with unknown RUL last when ascending', () => {
    // 'health' is the default column, so one click on 'rul' selects it in
    // ascending order. Engine c has a null RUL and must sort last: unknown
    // remaining life is not the same as urgent.
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.setSort('rul');

    const state = useFleetStore.getState();
    expect(state.sortKey).toBe('rul');
    expect(state.sortOrder).toBe('asc');
    expect(selectFleetView(state).rows.at(-1)?.engine_id).toBe('c');
  });

  it('filters by health band', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.setBandFilter('CRITICAL');
    const view = selectFleetView(useFleetStore.getState());
    expect(view.rows).toHaveLength(1);
    expect(view.filtered).toBe(1);
    expect(view.total).toBe(3);
  });

  it('filters to alerting engines only', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.toggleAnomaliesOnly();
    const view = selectFleetView(useFleetStore.getState());
    expect(view.rows.map((r) => r.engine_id)).toEqual(['b']);
  });

  it('searches by tail number, case-insensitively', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.setSearch('at-0002');
    expect(selectFleetView(useFleetStore.getState()).rows).toHaveLength(1);
  });

  it('searches by exact unit number', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.setSearch('3');
    const view = selectFleetView(useFleetStore.getState());
    expect(view.rows.map((r) => r.engine_id)).toEqual(['c']);
  });

  it('returns an empty result rather than throwing when nothing matches', () => {
    const store = useFleetStore.getState();
    store.applyFleetSnapshot(summary(fleet));
    store.setSearch('nonexistent');
    expect(selectFleetView(useFleetStore.getState()).rows).toEqual([]);
  });

  it('respects the row limit', () => {
    const many = Array.from({ length: 300 }, (_, index) =>
      row({ engine_id: `e${index}`, unit_number: index }),
    );
    useFleetStore.getState().applyFleetSnapshot(summary(many));
    expect(selectFleetView(useFleetStore.getState(), 50).rows).toHaveLength(50);
  });
});
