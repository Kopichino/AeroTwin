'use client';

/**
 * Bridges websocket frames into the Zustand store (Doc 14 section 14.3).
 *
 * Kept as a hook rather than module-level side effects so the connection
 * follows component lifecycle and tests can mount it in isolation.
 */

import { useEffect } from 'react';

import type { FleetSummary, ServerFrame, SystemStats, TwinDetail } from '@/lib/types';
import { getWsClient } from '@/lib/ws-client';
import { useFleetStore } from '@/stores/fleet-store';

/** Route one frame to the correct store action. Exported for testing. */
export function routeFrame(frame: ServerFrame): void {
  const store = useFleetStore.getState();

  switch (frame.type) {
    case 'fleet.snapshot':
    case 'fleet.delta':
      store.applyFleetSnapshot(frame.payload as FleetSummary);
      break;

    case 'twin.snapshot':
    case 'twin.delta':
      store.applyTwinDelta(frame.payload as TwinDetail);
      break;

    case 'twin.event': {
      const payload = frame.payload as Record<string, unknown>;
      store.pushEvent({
        engine_id: String(payload.engine_id ?? ''),
        seq: Number(payload.seq ?? 0),
        cycle: Number(payload.cycle ?? 0),
        event_type: String(payload.event_type ?? ''),
        severity: String(payload.severity ?? 'INFO'),
        payload: (payload.payload as Record<string, unknown>) ?? {},
        received_at: Date.now(),
      });
      break;
    }

    case 'system.status':
    case 'system.snapshot':
      store.setSystem(frame.payload as SystemStats);
      break;

    // welcome / subscribed / pong carry no state; ignoring them is intentional.
    default:
      break;
  }
}

/** Connect, subscribe to fleet-wide channels, and stream into the store. */
export function useFleetStream(): void {
  useEffect(() => {
    const client = getWsClient();
    const setConnection = useFleetStore.getState().setConnection;

    const offStatus = client.onStatus((status, attempt) => setConnection(status, attempt));
    const offFrame = client.onFrame(routeFrame);
    const unsubscribeFleet = client.subscribe('fleet');
    const unsubscribeSystem = client.subscribe('system');

    client.connect();

    return () => {
      unsubscribeFleet();
      unsubscribeSystem();
      offFrame();
      offStatus();
    };
  }, []);
}

/** Subscribe to one engine's channel for as long as the component is mounted. */
export function useTwinStream(engineId: string | null): void {
  useEffect(() => {
    if (!engineId) return;
    const client = getWsClient();
    const unsubscribe = client.subscribe(`twin:${engineId}`);
    return unsubscribe;
  }, [engineId]);
}
