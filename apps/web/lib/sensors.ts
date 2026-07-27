/**
 * Sensor display metadata, mirroring `at_core.domain.sensors` (Doc 00 section 0.6).
 *
 * Only the charted subset is listed: these are the channels the twin engine
 * records into history, so the rest would render as empty tiles.
 */

export interface SensorMeta {
  symbol: string;
  description: string;
  unit: string;
  module: string;
}

export const CHARTED_SENSOR_META: Record<string, SensorMeta> = {
  s2: { symbol: 'T24', description: 'Total temperature at LPC outlet', unit: '°R', module: 'LPC' },
  s3: { symbol: 'T30', description: 'Total temperature at HPC outlet', unit: '°R', module: 'HPC' },
  s4: { symbol: 'T50', description: 'Total temperature at LPT outlet', unit: '°R', module: 'LPT' },
  s7: { symbol: 'P30', description: 'Total pressure at HPC outlet', unit: 'psia', module: 'HPC' },
  s8: { symbol: 'Nf', description: 'Physical fan speed', unit: 'rpm', module: 'FAN' },
  s9: { symbol: 'Nc', description: 'Physical core speed', unit: 'rpm', module: 'HPC' },
  s11: { symbol: 'Ps30', description: 'Static pressure at HPC outlet', unit: 'psia', module: 'HPC' },
  s12: { symbol: 'phi', description: 'Fuel flow per unit Ps30', unit: 'pps/psi', module: 'COMBUSTOR' },
  s15: { symbol: 'BPR', description: 'Bypass ratio', unit: '—', module: 'FAN' },
  s17: { symbol: 'htBleed', description: 'Bleed enthalpy', unit: '—', module: 'HPC' },
  s20: { symbol: 'W31', description: 'HPT coolant bleed', unit: 'lbm/s', module: 'HPT' },
  s21: { symbol: 'W32', description: 'LPT coolant bleed', unit: 'lbm/s', module: 'LPT' },
};
