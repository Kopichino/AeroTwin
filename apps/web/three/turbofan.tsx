'use client';

/**
 * Procedural turbofan geometry (Doc 06 section 6.7).
 *
 * Built from primitives rather than loading a GLB. A modelled asset would look
 * better, but it would also be a multi-megabyte binary with no provenance in
 * the repository, and it would need a named-mesh convention that silently
 * breaks whenever the model is re-exported. Generating the geometry keeps the
 * module -> mesh mapping explicit in code, keeps the bundle small, and means
 * the layout is reviewable in a diff.
 *
 * The seven meshes correspond exactly to `EngineModule` (Doc 08 section 8.5),
 * so component health maps onto geometry without a translation table.
 */

import { Instance, Instances } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useMemo, useRef, useState } from 'react';
import type { Group, Mesh, MeshStandardMaterial } from 'three';
import { Color, MathUtils } from 'three';

import type { HealthBand } from '@/lib/types';

/** Axial layout: gas flows left (fan) to right (nozzle). */
export interface ModuleSpec {
  id: string;
  label: string;
  /** Centre position along the engine axis. */
  x: number;
  /** Half-length along the axis. */
  length: number;
  radiusFront: number;
  radiusBack: number;
}

/**
 * Axial extents, laid out contiguously from the fan face.
 *
 * Positions are *derived* from lengths rather than hand-written. Hand-placed
 * centres drifted out of sync with the half-lengths and left five pairs of
 * modules intersecting by up to 0.35 units, which z-fights when two translucent
 * surfaces overlap.
 */
const MODULE_EXTENTS: {
  id: string;
  label: string;
  length: number;
  radiusFront: number;
  radiusBack: number;
  gap?: number;
}[] = [
  { id: 'FAN', label: 'Fan', length: 0.34, radiusFront: 1.5, radiusBack: 1.5 },
  { id: 'LPC', label: 'LP Compressor', length: 0.7, radiusFront: 0.95, radiusBack: 0.8, gap: 0.14 },
  { id: 'HPC', label: 'HP Compressor', length: 0.85, radiusFront: 0.8, radiusBack: 0.55, gap: 0.06 },
  { id: 'COMBUSTOR', label: 'Combustor', length: 0.4, radiusFront: 0.6, radiusBack: 0.62, gap: 0.06 },
  { id: 'HPT', label: 'HP Turbine', length: 0.35, radiusFront: 0.66, radiusBack: 0.74, gap: 0.06 },
  { id: 'LPT', label: 'LP Turbine', length: 0.5, radiusFront: 0.8, radiusBack: 0.95, gap: 0.06 },
  { id: 'NOZZLE', label: 'Nozzle', length: 0.45, radiusFront: 0.9, radiusBack: 0.62, gap: 0.06 },
];

export const MODULE_LAYOUT: ModuleSpec[] = (() => {
  const total =
    MODULE_EXTENTS.reduce((sum, m) => sum + m.length * 2 + (m.gap ?? 0), 0) -
    (MODULE_EXTENTS[0]?.gap ?? 0);
  let cursor = -total / 2;

  return MODULE_EXTENTS.map((module, index) => {
    if (index > 0) cursor += module.gap ?? 0;
    const centre = cursor + module.length;
    cursor += module.length * 2;
    return {
      id: module.id,
      label: module.label,
      x: Number(centre.toFixed(4)),
      length: module.length,
      radiusFront: module.radiusFront,
      radiusBack: module.radiusBack,
    };
  });
})();

/** Fan centre, reused by the rotor so it stays attached to the fan module. */
export const FAN_X = MODULE_LAYOUT[0]?.x ?? -3.1;

const HEALTH_COLOURS = {
  HEALTHY: '#22c98a',
  WATCH: '#7fd1a6',
  WARNING: '#f5b942',
  CRITICAL: '#ff4d4d',
} as const;

export function bandFor(score: number): HealthBand {
  if (score >= 80) return 'HEALTHY';
  if (score >= 60) return 'WATCH';
  if (score >= 35) return 'WARNING';
  return 'CRITICAL';
}

/**
 * Interpolate green -> amber -> red across the health range.
 *
 * A continuous ramp rather than four discrete colours: an engine drifting from
 * 81 to 79 should look slightly worse, not suddenly change category. The
 * discrete bands remain in the text labels where the threshold matters.
 */
export function healthColour(score: number): Color {
  const clamped = MathUtils.clamp(score, 0, 100) / 100;
  const critical = new Color(HEALTH_COLOURS.CRITICAL);
  const warning = new Color(HEALTH_COLOURS.WARNING);
  const healthy = new Color(HEALTH_COLOURS.HEALTHY);

  return clamped < 0.5
    ? critical.clone().lerp(warning, clamped / 0.5)
    : warning.clone().lerp(healthy, (clamped - 0.5) / 0.5);
}

interface ModuleMeshProps {
  spec: ModuleSpec;
  score: number;
  selected: boolean;
  hovered: boolean;
  exploded: number;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}

function ModuleMesh({
  spec,
  score,
  selected,
  hovered,
  exploded,
  onSelect,
  onHover,
}: ModuleMeshProps) {
  const mesh = useRef<Mesh>(null);
  const colour = useMemo(() => healthColour(score), [score]);

  // A failing module glows harder and looks rougher, so severity reads at a
  // glance without needing to find the label.
  const severity = 1 - MathUtils.clamp(score, 0, 100) / 100;
  const baseEmissive = 0.12 + severity * 0.55;

  useFrame(({ clock }) => {
    if (!mesh.current) return;
    // `Mesh.material` is typed as a union with an array; these meshes are
    // declared with a single material, so narrow rather than cast blindly.
    const material = mesh.current.material as MeshStandardMaterial;

    // Only critical modules pulse. Animating everything would turn the whole
    // engine into a light show and destroy the signal.
    const pulse =
      score < 35 ? 0.22 * (0.5 + 0.5 * Math.sin(clock.elapsedTime * 5)) : 0;
    const focus = selected ? 0.3 : hovered ? 0.16 : 0;

    material.emissiveIntensity = MathUtils.lerp(
      material.emissiveIntensity,
      baseEmissive + pulse + focus,
      0.12,
    );
    material.emissive.lerp(colour, 0.12);
    material.color.lerp(colour, 0.12);
    material.roughness = MathUtils.lerp(material.roughness, 0.32 + severity * 0.42, 0.12);

    // Exploded view fans the modules outward along the axis, proportional to
    // their distance from the centre so the ordering stays legible.
    mesh.current.position.x = MathUtils.lerp(
      mesh.current.position.x,
      spec.x * (1 + exploded * 0.45),
      0.15,
    );
  });

  return (
    <mesh
      ref={mesh}
      position={[spec.x, 0, 0]}
      rotation={[0, 0, -Math.PI / 2]}
      castShadow
      receiveShadow
      onClick={(event) => {
        event.stopPropagation();
        onSelect(spec.id);
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        onHover(spec.id);
      }}
      onPointerOut={() => onHover(null)}
    >
      <cylinderGeometry
        args={[spec.radiusBack, spec.radiusFront, spec.length * 2, 40, 1, false]}
      />
      <meshStandardMaterial
        color={colour}
        emissive={colour}
        emissiveIntensity={baseEmissive}
        metalness={0.65}
        roughness={0.4}
      />
    </mesh>
  );
}

/** Fan blades, rotating at a speed derived from the physical fan speed. */
function FanRotor({ rpmNormalised, exploded }: { rpmNormalised: number; exploded: number }) {
  const group = useRef<Group>(null);
  const blades = 18;

  useFrame((_, delta) => {
    if (!group.current) return;
    // Map normalised RPM onto a visible range. Real fan speed would be a blur;
    // this is fast enough to read as "spinning" and slow enough to see blades.
    const speed = MathUtils.lerp(1.6, 9.0, MathUtils.clamp(rpmNormalised, 0, 1));
    group.current.rotation.x += speed * delta;
    group.current.position.x = MathUtils.lerp(
      group.current.position.x,
      FAN_X * (1 + exploded * 0.45),
      0.15,
    );
  });

  return (
    <group ref={group} position={[FAN_X, 0, 0]}>
      <Instances limit={blades} castShadow>
        <boxGeometry args={[0.06, 1.05, 0.24]} />
        <meshStandardMaterial color="#c9d2e0" metalness={0.85} roughness={0.25} />
        {Array.from({ length: blades }, (_, index) => {
          const angle = (index / blades) * Math.PI * 2;
          return (
            <Instance
              key={index}
              position={[0, Math.cos(angle) * 0.95, Math.sin(angle) * 0.95]}
              rotation={[angle, 0, 0.35]}
            />
          );
        })}
      </Instances>
      <mesh>
        <sphereGeometry args={[0.28, 20, 20]} />
        <meshStandardMaterial color="#8b94a6" metalness={0.9} roughness={0.2} />
      </mesh>
    </group>
  );
}

/** Semi-transparent nacelle. Hidden in X-ray so the core is visible. */
function Nacelle({ xray, exploded }: { xray: boolean; exploded: number }) {
  const mesh = useRef<Mesh>(null);

  useFrame(() => {
    if (!mesh.current) return;
    const material = mesh.current.material as MeshStandardMaterial;
    material.opacity = MathUtils.lerp(material.opacity, xray ? 0.04 : 0.16, 0.1);
    mesh.current.scale.x = MathUtils.lerp(mesh.current.scale.x, 1 + exploded * 0.4, 0.15);
  });

  return (
    <mesh ref={mesh} rotation={[0, 0, -Math.PI / 2]} position={[-0.2, 0, 0]}>
      <cylinderGeometry args={[1.35, 1.62, 7.2, 48, 1, true]} />
      <meshStandardMaterial
        color="#8fa3c4"
        transparent
        opacity={0.16}
        metalness={0.5}
        roughness={0.35}
        side={2}
      />
    </mesh>
  );
}

function Shaft({ exploded }: { exploded: number }) {
  const mesh = useRef<Mesh>(null);
  useFrame(() => {
    if (!mesh.current) return;
    mesh.current.scale.y = MathUtils.lerp(mesh.current.scale.y, 1 + exploded * 0.42, 0.15);
  });
  return (
    <mesh ref={mesh} rotation={[0, 0, -Math.PI / 2]}>
      <cylinderGeometry args={[0.16, 0.16, 7.4, 16]} />
      <meshStandardMaterial color="#5c6475" metalness={0.9} roughness={0.3} />
    </mesh>
  );
}

/** Pulsing marker above a module with an active anomaly. */
function Hotspot({ x, exploded }: { x: number; exploded: number }) {
  const group = useRef<Group>(null);

  useFrame(({ clock }) => {
    if (!group.current) return;
    const t = clock.elapsedTime;
    const scale = 1 + 0.25 * Math.sin(t * 4);
    group.current.scale.setScalar(scale);
    group.current.position.x = MathUtils.lerp(
      group.current.position.x,
      x * (1 + exploded * 0.45),
      0.15,
    );
    group.current.position.y = 1.75 + Math.sin(t * 2) * 0.05;
  });

  return (
    <group ref={group} position={[x, 1.75, 0]}>
      <mesh>
        <sphereGeometry args={[0.12, 16, 16]} />
        <meshBasicMaterial color="#a855f7" />
      </mesh>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.18, 0.24, 24]} />
        <meshBasicMaterial color="#a855f7" transparent opacity={0.55} side={2} />
      </mesh>
    </group>
  );
}

export interface TurbofanProps {
  components: Record<string, number>;
  fanSpeedNormalised: number;
  anomalyModule: string | null;
  exploded: boolean;
  xray: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export function Turbofan({
  components,
  fanSpeedNormalised,
  anomalyModule,
  exploded,
  xray,
  selected,
  onSelect,
}: TurbofanProps) {
  const [hovered, setHovered] = useState<string | null>(null);
  const explodeRef = useRef(0);
  const group = useRef<Group>(null);

  useFrame((_, delta) => {
    explodeRef.current = MathUtils.lerp(explodeRef.current, exploded ? 1 : 0, 0.1);
    // A slow idle rotation makes the geometry read as three-dimensional without
    // the user having to drag.
    if (group.current && !selected) group.current.rotation.y += delta * 0.08;
  });

  const anomalySpec = MODULE_LAYOUT.find((module) => module.id === anomalyModule);

  return (
    <group ref={group} onPointerMissed={() => onSelect(null)}>
      <Shaft exploded={explodeRef.current} />
      {MODULE_LAYOUT.map((spec) => (
        <ModuleMesh
          key={spec.id}
          spec={spec}
          score={components[spec.id] ?? 100}
          selected={selected === spec.id}
          hovered={hovered === spec.id}
          exploded={explodeRef.current}
          onSelect={onSelect}
          onHover={setHovered}
        />
      ))}
      <FanRotor rpmNormalised={fanSpeedNormalised} exploded={explodeRef.current} />
      <Nacelle xray={xray} exploded={explodeRef.current} />
      {anomalySpec ? <Hotspot x={anomalySpec.x} exploded={explodeRef.current} /> : null}
    </group>
  );
}
