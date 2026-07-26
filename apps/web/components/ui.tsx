/**
 * Design system primitives (Doc 06 section 6.4).
 *
 * Small and dependency-free by design: every component here is presentational,
 * takes plain props, and holds no state, which keeps them trivially testable
 * and safe to render on the server.
 */

import type { ReactNode } from 'react';

import { BAND_META, type HealthBand } from '@/lib/types';

export function bandColour(band: HealthBand | null | undefined): string {
  if (!band) return 'var(--text-tertiary)';
  return `var(${BAND_META[band].varName})`;
}

/** Band label with a colour swatch. Colour is never the only signal (WCAG 1.4.1). */
export function HealthPill({ band }: { band: HealthBand }): ReactNode {
  const colour = bandColour(band);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-sm px-2 py-1 font-mono text-[10px] tracking-wide"
      style={{ color: colour, background: `color-mix(in srgb, ${colour} 12%, transparent)` }}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: colour }} />
      {band}
    </span>
  );
}

/** Horizontal health bar. Purely decorative; the numeric value sits alongside. */
export function HealthBar({
  value,
  band,
  width = 96,
}: {
  value: number;
  band: HealthBand;
  width?: number;
}): ReactNode {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      aria-hidden
      className="relative h-1.5 overflow-hidden rounded-full bg-white/[0.07]"
      style={{ width }}
    >
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-[width,background-color] duration-500 ease-spring"
        style={{ width: `${Math.max(2, clamped)}%`, background: bandColour(band) }}
      />
    </div>
  );
}

export function StatTile({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: string;
}): ReactNode {
  return (
    <div className="glass-panel rounded-md px-4 py-3.5">
      <div className="text-[11px] uppercase tracking-[0.08em] text-tertiary">{label}</div>
      <div
        className="tabular mt-1.5 font-mono text-2xl font-semibold leading-tight"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-[11px] text-tertiary">{sub}</div> : null}
    </div>
  );
}

export function ConnectionBadge({
  status,
  attempt,
}: {
  status: string;
  attempt: number;
}): ReactNode {
  const isLive = status === 'open';
  const colour = isLive
    ? 'var(--health-good)'
    : status === 'closed'
      ? 'var(--health-crit)'
      : 'var(--health-warn)';
  const label = isLive
    ? 'live'
    : status === 'reconnecting'
      ? `reconnecting${attempt > 1 ? ` (${attempt})` : ''}`
      : status;

  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-1.5 rounded-sm border px-2.5 py-1 font-mono text-[11px]"
      style={{ color: colour, borderColor: `color-mix(in srgb, ${colour} 35%, transparent)` }}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${isLive ? 'animate-pulse' : ''}`}
        style={{ background: colour }}
      />
      {label}
    </span>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }): ReactNode {
  return (
    <div className="px-6 py-16 text-center">
      <p className="text-sm text-secondary">{title}</p>
      {hint ? <p className="mt-1 text-xs text-tertiary">{hint}</p> : null}
    </div>
  );
}

/** Skeleton rows. Matches the final table geometry so nothing shifts on load. */
export function TableSkeleton({ rows = 8 }: { rows?: number }): ReactNode {
  return (
    <div className="divide-y divide-white/[0.04]" aria-hidden>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-4 px-3 py-2.5">
          <div className="h-3 w-20 animate-pulse rounded bg-white/[0.06]" />
          <div className="h-3 w-10 animate-pulse rounded bg-white/[0.04]" />
          <div className="h-1.5 flex-1 animate-pulse rounded-full bg-white/[0.04]" />
          <div className="h-3 w-16 animate-pulse rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}
