import { describe, expect, it } from 'vitest';

import { normaliseFanSpeed } from '@/three/engine-scene';
import { MODULE_LAYOUT, bandFor, healthColour } from '@/three/turbofan';

/**
 * The geometry itself is not asserted pixel by pixel; what matters is that the
 * data-to-visual mapping is correct, because that mapping is what an engineer
 * reads off the screen.
 */

describe('module layout', () => {
  it('covers every tracked engine module', () => {
    const expected = ['FAN', 'LPC', 'HPC', 'COMBUSTOR', 'HPT', 'LPT', 'NOZZLE'];
    expect(MODULE_LAYOUT.map((module) => module.id)).toEqual(expected);
  });

  it('orders modules front to back along the gas path', () => {
    const positions = MODULE_LAYOUT.map((module) => module.x);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('gives every module positive extent', () => {
    for (const module of MODULE_LAYOUT) {
      expect(module.length).toBeGreaterThan(0);
      expect(module.radiusFront).toBeGreaterThan(0);
      expect(module.radiusBack).toBeGreaterThan(0);
    }
  });

  it('narrows through the compressor and widens through the turbine', () => {
    // Physical shape of a turbofan: the core contracts to the combustor and
    // expands again through the turbines.
    const hpc = MODULE_LAYOUT.find((m) => m.id === 'HPC');
    const lpt = MODULE_LAYOUT.find((m) => m.id === 'LPT');
    expect(hpc?.radiusBack).toBeLessThan(hpc?.radiusFront ?? 0);
    expect(lpt?.radiusBack).toBeGreaterThan(lpt?.radiusFront ?? 0);
  });

  it('does not overlap adjacent modules', () => {
    for (let index = 1; index < MODULE_LAYOUT.length; index += 1) {
      const previous = MODULE_LAYOUT[index - 1];
      const current = MODULE_LAYOUT[index];
      if (!previous || !current) continue;
      expect(current.x - current.length).toBeGreaterThanOrEqual(
        previous.x + previous.length - 0.01,
      );
    }
  });
});

describe('health banding', () => {
  it.each([
    [100, 'HEALTHY'],
    [80, 'HEALTHY'],
    [79.9, 'WATCH'],
    [60, 'WATCH'],
    [59.9, 'WARNING'],
    [35, 'WARNING'],
    [34.9, 'CRITICAL'],
    [0, 'CRITICAL'],
  ])('maps %d to %s', (score, expected) => {
    // Thresholds must match at_core.domain.health exactly, or the 3D view and
    // the tables disagree about the same engine.
    expect(bandFor(score)).toBe(expected);
  });
});

describe('health colour ramp', () => {
  it('is green at full health and red at zero', () => {
    expect(healthColour(100).getHexString()).toBe('22c98a');
    expect(healthColour(0).getHexString()).toBe('ff4d4d');
  });

  it('passes through amber at the midpoint', () => {
    expect(healthColour(50).getHexString()).toBe('f5b942');
  });

  it('is continuous rather than stepped', () => {
    // A module drifting 81 -> 79 should look slightly worse, not jump category.
    const above = healthColour(81);
    const below = healthColour(79);
    const distance =
      Math.abs(above.r - below.r) + Math.abs(above.g - below.g) + Math.abs(above.b - below.b);
    expect(distance).toBeLessThan(0.1);
  });

  it('darkens toward red monotonically as health falls', () => {
    const scores = [100, 75, 50, 25, 0];
    const greens = scores.map((score) => healthColour(score).g);
    expect(greens).toEqual([...greens].sort((a, b) => b - a));
  });

  it('clamps values outside the valid range', () => {
    expect(healthColour(-50).getHexString()).toBe('ff4d4d');
    expect(healthColour(500).getHexString()).toBe('22c98a');
  });
});

describe('fan speed normalisation', () => {
  it('maps the observed FD001 range onto 0..1', () => {
    expect(normaliseFanSpeed(2387.9)).toBeCloseTo(0, 3);
    expect(normaliseFanSpeed(2388.6)).toBeCloseTo(1, 3);
  });

  it('clamps values outside the observed range', () => {
    expect(normaliseFanSpeed(1000)).toBe(0);
    expect(normaliseFanSpeed(9999)).toBe(1);
  });

  it('falls back to mid-range for missing or invalid readings', () => {
    // A missing sensor should leave the fan turning at a plausible speed
    // rather than stopping dead, which would read as an engine failure.
    expect(normaliseFanSpeed(undefined)).toBe(0.5);
    expect(normaliseFanSpeed(Number.NaN)).toBe(0.5);
    expect(normaliseFanSpeed(Number.POSITIVE_INFINITY)).toBe(0.5);
  });

  it('increases monotonically with fan speed', () => {
    const samples = [2387.9, 2388.1, 2388.3, 2388.5];
    const normalised = samples.map(normaliseFanSpeed);
    expect(normalised).toEqual([...normalised].sort((a, b) => a - b));
  });
});
