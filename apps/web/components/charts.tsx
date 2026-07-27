'use client';

/**
 * SVG chart primitives (Doc 15 section 15.2.3).
 *
 * Hand-rolled rather than pulling in a charting library. These are three
 * specific chart shapes over a few hundred points; a general-purpose library
 * would add ~150 KB to the bundle to draw polylines we can express in a dozen
 * lines. Plain SVG also keeps every element inspectable and styleable by the
 * design tokens.
 *
 * Every chart carries an accessible table fallback, because a screen reader
 * cannot read a polyline.
 */

import { useId, type ReactNode } from 'react';

export interface Point {
  x: number;
  y: number;
}

interface Extent {
  min: number;
  max: number;
}

function extent(values: number[], pad = 0.05): Extent {
  if (values.length === 0) return { min: 0, max: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return { min: min - 1, max: max + 1 };
  const padding = (max - min) * pad;
  return { min: min - padding, max: max + padding };
}

function scale(value: number, from: Extent, size: number, invert = false): number {
  const ratio = (value - from.min) / (from.max - from.min || 1);
  return invert ? size - ratio * size : ratio * size;
}

function path(points: Point[], xs: Extent, ys: Extent, width: number, height: number): string {
  return points
    .map((point, index) => {
      const x = scale(point.x, xs, width);
      const y = scale(point.y, ys, height, true);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export interface BandSeries {
  lower: Point[];
  upper: Point[];
}

export function LineChart({
  series,
  band,
  colour = 'var(--accent)',
  bandColour = 'var(--accent)',
  height = 160,
  width = 720,
  label,
  yFormat = (value: number) => value.toFixed(0),
  yDomain,
  markers,
}: {
  series: Point[];
  band?: BandSeries;
  colour?: string;
  bandColour?: string;
  height?: number;
  width?: number;
  label: string;
  yFormat?: (value: number) => string;
  /** Force a y-range, e.g. health is always 0–100 regardless of observed values. */
  yDomain?: [number, number];
  markers?: { x: number; colour: string; title: string }[];
}): ReactNode {
  const gradientId = useId();

  if (series.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-xs text-tertiary"
        style={{ height }}
      >
        Collecting data…
      </div>
    );
  }

  const xs = extent(series.map((point) => point.x), 0);
  const allY = [
    ...series.map((point) => point.y),
    ...(band ? [...band.lower.map((p) => p.y), ...band.upper.map((p) => p.y)] : []),
  ];
  const ys = yDomain ? { min: yDomain[0], max: yDomain[1] } : extent(allY);

  const bandPath = band
    ? `${path(band.upper, xs, ys, width, height)} L${band.lower
        .slice()
        .reverse()
        .map((point) => `${scale(point.x, xs, width).toFixed(1)},${scale(point.y, ys, height, true).toFixed(1)}`)
        .join(' L')} Z`
    : null;

  const gridLines = [0, 0.25, 0.5, 0.75, 1];

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={label}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colour} stopOpacity="0.18" />
            <stop offset="100%" stopColor={colour} stopOpacity="0" />
          </linearGradient>
        </defs>

        {gridLines.map((fraction) => (
          <line
            key={fraction}
            x1={0}
            x2={width}
            y1={fraction * height}
            y2={fraction * height}
            stroke="rgba(255,255,255,0.05)"
            strokeWidth={1}
          />
        ))}

        {markers?.map((marker, index) => (
          <line
            key={index}
            x1={scale(marker.x, xs, width)}
            x2={scale(marker.x, xs, width)}
            y1={0}
            y2={height}
            stroke={marker.colour}
            strokeWidth={1}
            strokeDasharray="3 3"
            opacity={0.6}
          >
            <title>{marker.title}</title>
          </line>
        ))}

        {bandPath ? <path d={bandPath} fill={bandColour} opacity={0.14} /> : null}

        <path
          d={`${path(series, xs, ys, width, height)} L${width},${height} L0,${height} Z`}
          fill={`url(#${gradientId})`}
        />
        <path
          d={path(series, xs, ys, width, height)}
          fill="none"
          stroke={colour}
          strokeWidth={1.75}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>

      <div className="mt-1 flex justify-between font-mono text-[10px] text-tertiary">
        <span>{yFormat(ys.min)}</span>
        <span>
          cycle {Math.round(xs.min)}–{Math.round(xs.max)}
        </span>
        <span>{yFormat(ys.max)}</span>
      </div>

      {/* Charts are not readable by assistive technology; the data is. */}
      <details className="mt-1">
        <summary className="cursor-pointer text-[10px] text-tertiary hover:text-secondary">
          View as table
        </summary>
        <table className="mt-1 w-full text-[10px]">
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              <th scope="col" className="text-left text-tertiary">Cycle</th>
              <th scope="col" className="text-right text-tertiary">Value</th>
            </tr>
          </thead>
          <tbody>
            {series.slice(-12).map((point) => (
              <tr key={point.x}>
                <td className="tabular font-mono">{point.x}</td>
                <td className="tabular text-right font-mono">{yFormat(point.y)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Compact inline trend line, sized for a table cell or sensor tile. */
export function Sparkline({
  values,
  colour = 'var(--accent)',
  width = 120,
  height = 28,
  label,
}: {
  values: number[];
  colour?: string;
  width?: number;
  height?: number;
  label: string;
}): ReactNode {
  if (values.length < 2) {
    return <div style={{ width, height }} aria-hidden />;
  }

  const ys = extent(values);
  const step = width / (values.length - 1);
  const d = values
    .map((value, index) => {
      const x = index * step;
      const y = scale(value, ys, height, true);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{ width, height }}
      role="img"
      aria-label={label}
    >
      <path
        d={d}
        fill="none"
        stroke={colour}
        strokeWidth={1.5}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** Horizontal bars for XAI attribution. */
export function AttributionBars({
  items,
}: {
  items: { name: string; value: number; direction: string; module: string }[];
}): ReactNode {
  if (items.length === 0) {
    return <p className="text-xs text-tertiary">No attribution available.</p>;
  }

  const peak = Math.max(...items.map((item) => item.value)) || 1;

  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item.name} className="flex items-center gap-2.5">
          <span className="w-16 shrink-0 font-mono text-[11px]">{item.name}</span>
          <span className="relative h-3 flex-1 overflow-hidden rounded-sm bg-white/[0.05]">
            <span
              className="absolute inset-y-0 left-0 rounded-sm transition-[width] duration-500 ease-spring"
              style={{
                width: `${(item.value / peak) * 100}%`,
                background: item.direction === 'up' ? 'var(--health-warn)' : 'var(--accent)',
              }}
            />
          </span>
          <span aria-hidden className="w-4 text-center text-[11px] text-tertiary">
            {item.direction === 'up' ? '↑' : '↓'}
          </span>
          <span className="w-20 shrink-0 font-mono text-[10px] text-tertiary">
            {item.module}
          </span>
        </li>
      ))}
    </ul>
  );
}
