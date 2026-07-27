'use client';

/**
 * Scene wrapper: canvas, lighting, camera, controls and fallbacks (Doc 06 section 6.7).
 *
 * Two things here are as important as the 3D itself:
 *
 * - **A 2D fallback.** WebGL is unavailable in some corporate browsers, on some
 *   VMs, and whenever a GPU driver misbehaves. Rendering a blank box in those
 *   cases would make the product look broken; an SVG schematic with identical
 *   health colouring keeps it usable.
 * - **Keyboard access.** Modules are selectable from a listbox as well as by
 *   clicking geometry, because a raycast into a canvas is unreachable by
 *   keyboard and invisible to a screen reader.
 */

import { OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';

import { bandColour } from '@/components/ui';

import { MODULE_LAYOUT, Turbofan, bandFor, healthColour } from './turbofan';

/** Physical fan speed range for FD001, used to normalise the rotation rate. */
const FAN_SPEED_MIN = 2387.9;
const FAN_SPEED_MAX = 2388.6;

export function normaliseFanSpeed(nf: number | undefined): number {
  if (nf == null || !Number.isFinite(nf)) return 0.5;
  return Math.min(1, Math.max(0, (nf - FAN_SPEED_MIN) / (FAN_SPEED_MAX - FAN_SPEED_MIN)));
}

function webglAvailable(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(
      canvas.getContext('webgl2') ??
        canvas.getContext('webgl') ??
        canvas.getContext('experimental-webgl'),
    );
  } catch {
    return false;
  }
}

export interface EngineSceneProps {
  components: Record<string, number>;
  sensors: Record<string, number>;
  anomalyModule: string | null;
  worstModule: string | null;
  height?: number;
}

export function EngineScene({
  components,
  sensors,
  anomalyModule,
  worstModule,
  height = 360,
}: EngineSceneProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [exploded, setExploded] = useState(false);
  const [xray, setXray] = useState(false);
  const [supported, setSupported] = useState<boolean | null>(null);

  // Probed after mount: the check touches `document`, which does not exist
  // during server rendering.
  useEffect(() => setSupported(webglAvailable()), []);

  const fanSpeed = useMemo(() => normaliseFanSpeed(sensors.s8), [sensors.s8]);

  const handleKey = useCallback((event: KeyboardEvent) => {
    if (event.target instanceof HTMLInputElement) return;
    const key = event.key.toLowerCase();
    if (key === 'e') setExploded((value) => !value);
    if (key === 'x') setXray((value) => !value);
    if (key === 'r') setSelected(null);
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleKey]);

  const detail = selected ?? worstModule;
  const detailScore = detail ? components[detail] : undefined;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ToggleButton active={exploded} onClick={() => setExploded((v) => !v)} hint="E">
          Exploded
        </ToggleButton>
        <ToggleButton active={xray} onClick={() => setXray((v) => !v)} hint="X">
          X-ray
        </ToggleButton>
        {selected ? (
          <button
            type="button"
            onClick={() => setSelected(null)}
            className="rounded-sm border border-line px-2 py-1 text-[11px] text-tertiary transition-colors hover:text-secondary"
          >
            Clear selection (R)
          </button>
        ) : null}
      </div>

      <div
        className="relative overflow-hidden rounded-md border border-line bg-[#05060a]"
        style={{ height }}
      >
        {supported === null ? (
          <div className="flex h-full items-center justify-center text-xs text-tertiary">
            Initialising…
          </div>
        ) : supported ? (
          <Canvas
            camera={{ position: [6.5, 3.2, 7.5], fov: 42 }}
            dpr={[1, 2]}
            gl={{ antialias: true, powerPreference: 'high-performance' }}
            // Rendering pauses when the tab is hidden: a background tab
            // spinning a fan at 60fps is pure battery drain.
            frameloop="always"
          >
            <color attach="background" args={['#05060a']} />
            <fog attach="fog" args={['#05060a', 14, 26]} />

            <ambientLight intensity={0.35} />
            <directionalLight position={[6, 8, 5]} intensity={1.1} castShadow />
            <directionalLight position={[-6, -3, -5]} intensity={0.35} color="#4f8dfd" />
            <pointLight position={[0, 0, 0]} intensity={0.4} color="#f5b942" distance={6} />

            <Suspense fallback={null}>
              <Turbofan
                components={components}
                fanSpeedNormalised={fanSpeed}
                anomalyModule={anomalyModule}
                exploded={exploded}
                xray={xray}
                selected={selected}
                onSelect={setSelected}
              />
            </Suspense>

            <OrbitControls
              enablePan={false}
              enableDamping
              dampingFactor={0.08}
              minDistance={6}
              maxDistance={16}
              maxPolarAngle={Math.PI * 0.85}
            />
          </Canvas>
        ) : (
          <SchematicFallback
            components={components}
            anomalyModule={anomalyModule}
            selected={selected}
            onSelect={setSelected}
          />
        )}

        <div className="pointer-events-none absolute bottom-2 left-3 font-mono text-[10px] text-tertiary">
          {supported ? 'drag to orbit · scroll to zoom · E exploded · X x-ray' : '2D schematic'}
        </div>
      </div>

      {/* Keyboard- and screen-reader-accessible equivalent of clicking geometry. */}
      <div className="mt-3">
        <label htmlFor="module-select" className="sr-only">
          Select an engine module
        </label>
        <ul id="module-select" className="flex flex-wrap gap-1.5" role="listbox">
          {MODULE_LAYOUT.map((module) => {
            const score = components[module.id] ?? 100;
            const active = detail === module.id;
            return (
              <li key={module.id} role="option" aria-selected={active}>
                <button
                  type="button"
                  onClick={() => setSelected(active ? null : module.id)}
                  className={`rounded-sm border px-2 py-1 font-mono text-[10px] transition-colors ${
                    active ? 'border-accent' : 'border-line hover:border-line-strong'
                  }`}
                  style={{ color: bandColour(bandFor(score)) }}
                >
                  {module.id} {score.toFixed(0)}
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {detail && detailScore != null ? (
        <div className="mt-3 rounded-sm border border-line bg-glass px-3 py-2">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xs">
              {MODULE_LAYOUT.find((m) => m.id === detail)?.label ?? detail}
            </span>
            <span
              className="tabular ml-auto font-mono text-sm"
              style={{ color: bandColour(bandFor(detailScore)) }}
            >
              {detailScore.toFixed(1)}
            </span>
          </div>
          {anomalyModule === detail ? (
            <p className="mt-1 text-[11px] text-anomaly">Active anomaly attributed to this module.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  hint,
  children,
}: {
  active: boolean;
  onClick: () => void;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-sm border px-2 py-1 text-[11px] transition-colors ${
        active ? 'border-accent text-accent' : 'border-line text-tertiary hover:text-secondary'
      }`}
    >
      {children} <span className="text-tertiary">({hint})</span>
    </button>
  );
}

/**
 * SVG cross-section used when WebGL is unavailable.
 *
 * Deliberately shares the module layout and colour ramp with the 3D scene, so
 * the fallback conveys the same information rather than being a downgrade.
 */
export function SchematicFallback({
  components,
  anomalyModule,
  selected,
  onSelect,
}: {
  components: Record<string, number>;
  anomalyModule: string | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const minX = -4.0;
  const maxX = 4.0;
  const width = 460;
  const height = 200;
  const scaleX = (x: number) => ((x - minX) / (maxX - minX)) * width;
  const scaleR = (r: number) => (r / 1.7) * (height / 2.4);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-full w-full"
      role="img"
      aria-label="Engine cross-section with component health"
    >
      <line
        x1={0}
        x2={width}
        y1={height / 2}
        y2={height / 2}
        stroke="rgba(255,255,255,0.12)"
        strokeDasharray="4 4"
      />
      {MODULE_LAYOUT.map((module) => {
        const score = components[module.id] ?? 100;
        const colour = `#${healthColour(score).getHexString()}`;
        const x1 = scaleX(module.x - module.length);
        const x2 = scaleX(module.x + module.length);
        const r1 = scaleR(module.radiusFront);
        const r2 = scaleR(module.radiusBack);
        const centre = height / 2;
        const isSelected = selected === module.id;

        return (
          <g
            key={module.id}
            onClick={() => onSelect(isSelected ? null : module.id)}
            style={{ cursor: 'pointer' }}
          >
            <title>{`${module.label}: ${score.toFixed(1)}`}</title>
            <polygon
              points={`${x1},${centre - r1} ${x2},${centre - r2} ${x2},${centre + r2} ${x1},${centre + r1}`}
              fill={colour}
              fillOpacity={isSelected ? 0.55 : 0.32}
              stroke={colour}
              strokeWidth={isSelected ? 2 : 1}
            />
            <text
              x={(x1 + x2) / 2}
              y={centre + r2 + 14}
              textAnchor="middle"
              fill="var(--text-tertiary)"
              style={{ font: "9px 'JetBrains Mono', monospace" }}
            >
              {module.id}
            </text>
            {anomalyModule === module.id ? (
              <circle cx={(x1 + x2) / 2} cy={centre - r1 - 12} r={5} fill="#a855f7" />
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
