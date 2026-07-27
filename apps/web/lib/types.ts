/**
 * Wire types mirroring the backend payloads (Doc 12, Doc 13).
 *
 * Hand-written for M6. Doc 12 section 12.10 specifies generating these from the
 * committed OpenAPI schema; that codegen step lands with the typed REST client
 * so the generated output replaces this file wholesale rather than being
 * partially merged into it.
 */

export const HEALTH_BANDS = ['HEALTHY', 'WATCH', 'WARNING', 'CRITICAL'] as const;
export type HealthBand = (typeof HEALTH_BANDS)[number];

export type TwinStatus =
  | 'IDLE'
  | 'RUNNING'
  | 'PAUSED'
  | 'MAINTENANCE'
  | 'FAILED'
  | 'RETIRED';

export type Severity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

/** Compact row used by the fleet grid. */
export interface FleetRow {
  engine_id: string;
  tail_number: string | null;
  unit_number: number;
  status: TwinStatus;
  cycle: number;
  health_index: number;
  health_band: HealthBand;
  worst_module: string | null;
  rul_p50: number | null;
  rul_p10: number | null;
  rul_p90: number | null;
  model_backed: boolean;
  anomaly_score: number;
  anomaly_alerting: boolean;
}

export interface FleetSummary {
  engines: number;
  active: number;
  failed: number;
  avg_health: number;
  by_band: Record<HealthBand, number>;
  at_risk: number;
  speed: number;
  cycle?: number;
  engines_list?: FleetRow[];
}

export interface AnomalyInfo {
  score: number;
  severity: Severity;
  detector: string;
  module: string | null;
  alerting: boolean;
  sensors: { sensor: string; z: number }[];
}

/** Full per-engine payload. Deltas are state-replacing, so this is complete. */
export interface TwinDetail {
  engine_id: string;
  external_ref: string;
  tail_number: string | null;
  unit_number: number;
  status: TwinStatus;
  cycle: number;
  total_cycles: number;
  progress: number;
  health_index: number;
  health_band: HealthBand;
  degradation_rate: number;
  regime: number;
  components: Record<string, number>;
  worst_module: string | null;
  drivers: string[];
  rul_p50: number | null;
  rul_p10: number | null;
  rul_p90: number | null;
  model_id: string | null;
  anomaly_score: number;
  anomaly: AnomalyInfo | null;
  failure_prob: Record<string, number>;
  prediction_stale: boolean;
  sensors: Record<string, number>;
  seq: number;
}

/** One cycle of chart history (Doc 12 section 12.4). */
export interface HistorySample {
  cycle: number;
  health_index: number;
  health_band: HealthBand;
  rul_p50: number | null;
  rul_p10: number | null;
  rul_p90: number | null;
  anomaly_score: number;
  model_backed: boolean;
  sensors: Record<string, number>;
  components: Record<string, number>;
}

export interface InferenceStats {
  models_loaded: number;
  calls: number;
  failures: number;
  breaker_open: boolean;
  latency_p50_ms: number;
  latency_p95_ms: number;
}

export interface SystemStats {
  ticks: number;
  frames_published: number;
  engines: number;
  active: number;
  speed: number;
  tick_p50_ms: number;
  tick_p99_ms: number;
  uptime_s: number;
  inference?: InferenceStats;
}

/** Server frame envelope (Doc 13 section 13.2). */
export interface ServerFrame<T = unknown> {
  v?: number;
  id?: string;
  ts?: string;
  ch?: string;
  type: string;
  payload: T;
  trace_id?: string | null;
}

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

export const BAND_META: Record<HealthBand, { label: string; varName: string }> = {
  HEALTHY: { label: 'Healthy', varName: '--health-good' },
  WATCH: { label: 'Watch', varName: '--health-watch' },
  WARNING: { label: 'Warning', varName: '--health-warn' },
  CRITICAL: { label: 'Critical', varName: '--health-crit' },
};
