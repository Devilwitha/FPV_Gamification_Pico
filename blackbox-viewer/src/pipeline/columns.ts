// Maps named log columns (from .bbl decode or CSV) to normalised RawSeries.
import type { RawSeries, Vec3Arr } from '../types.ts';

export const GYRO_NAMES = [
  ['gyroADC[0]', 'gyroData[0]', 'gyro[0]'],
  ['gyroADC[1]', 'gyroData[1]', 'gyro[1]'],
  ['gyroADC[2]', 'gyroData[2]', 'gyro[2]'],
];
export const ACC_NAMES = [
  ['accSmooth[0]', 'accADC[0]', 'acc[0]'],
  ['accSmooth[1]', 'accADC[1]', 'acc[1]'],
  ['accSmooth[2]', 'accADC[2]', 'acc[2]'],
];
export const RC_NAMES = ['rcCommand[0]', 'rcCommand[1]', 'rcCommand[2]', 'rcCommand[3]'];
export const MOTOR_NAMES = Array.from({ length: 8 }, (_, i) => `motor[${i}]`);
export const NEEDED = new Set<string>([
  'time',
  ...GYRO_NAMES.flat(),
  ...ACC_NAMES.flat(),
  ...RC_NAMES,
  'setpoint[3]',
  ...MOTOR_NAMES,
  'vbatLatest',
]);

export type Columns = Map<string, Float64Array>;

export interface ColumnUnits {
  /** multiply time column by this to get seconds */
  timeToSec: number;
  /** multiply gyro by this to get deg/s */
  gyroToDeg: number;
  /** divide acc by this to get g (null = auto) */
  accScale: number | null;
  /** multiply vbatLatest by this to get volts (null = auto) */
  vbatToVolt?: number | null;
}

/** Battery classes of the leaderboard (cell count). */
export const CELL_CLASSES = [1, 2, 3, 4, 6, 8];

/**
 * Cell count from the resting voltage at the start of the log: the class whose per-cell voltage is
 * plausible (3.2 … 4.45 V) and closest to 3.95 V. 5S/7S are not classes, so e.g. 21 V counts as 6S.
 */
export function estimateCells(volts: number | null): number | null {
  if (volts === null || !Number.isFinite(volts) || volts < 2.5) return null;
  let best: number | null = null;
  let bestErr = Infinity;
  for (const cells of CELL_CLASSES) {
    const perCell = volts / cells;
    if (perCell < 3.2 || perCell > 4.45) continue;
    const err = Math.abs(perCell - 3.95);
    if (err < bestErr) [best, bestErr] = [cells, err];
  }
  return best;
}

/** vbatLatest unit for .bbl logs: Betaflight 4+/INAV/EmuFlight log 0.01 V, Betaflight 3 raw ADC. */
export function vbatToVoltFromHeaders(headers: Record<string, string>): number | null {
  const fw = `${headers['Firmware revision'] ?? ''} ${headers['Firmware type'] ?? ''}`;
  const bf = fw.match(/betaflight\s+(\d+)\./i);
  if ((bf && Number(bf[1]) >= 4) || /inav|emuflight|rotorflight/i.test(fw)) return 0.01;
  const scale = Number(headers['vbat_scale'] ?? headers['vbatscale']);
  if (bf && Number.isFinite(scale) && scale > 0) return (scale * 3.3) / 40950;
  return null;
}

/** Resting voltage: upper quartile of the first 3 s (before the pack sags under load). */
function startVoltage(t: Float64Array, raw: Float64Array | undefined, factor: number | null | undefined): number | null {
  if (!raw || !raw.length) return null;
  const values: number[] = [];
  for (let i = 0; i < raw.length && t[i] - t[0] <= 3; i++) if (raw[i] > 0) values.push(raw[i]);
  if (!values.length) return null;
  values.sort((a, b) => a - b);
  const value = values[Math.floor(values.length * 0.75)];
  // auto: centivolts (Betaflight 4) or volts (CSV export of Blackbox Explorer)
  const k = factor ?? (value > 100 ? 0.01 : 1);
  const volts = value * k;
  return volts >= 1 && volts <= 60 ? Math.round(volts * 100) / 100 : null;
}

const first = (cols: Columns, names: string[]) => {
  for (const n of names) {
    const c = cols.get(n);
    if (c) return c;
  }
  return null;
};

function median(values: ArrayLike<number>, step = 1): number {
  const arr: number[] = [];
  for (let i = 0; i < values.length; i += step) arr.push(values[i]);
  if (!arr.length) return 0;
  arr.sort((a, b) => a - b);
  return arr[arr.length >> 1];
}

/** Motor output range from header ("motorOutput:48,2047") or legacy min/max throttle. */
function motorRange(headers: Record<string, string>): [number, number] {
  const mo = headers['motorOutput']?.split(',').map(Number);
  if (mo && mo.length >= 2 && mo[1] > mo[0]) return [mo[0], mo[1]];
  const lo = Number(headers['minthrottle'] ?? 1000);
  const hi = Number(headers['maxthrottle'] ?? 2000);
  return hi > lo ? [lo, hi] : [1000, 2000];
}

export function columnsToSeries(cols: Columns, headers: Record<string, string>, units: ColumnUnits): RawSeries {
  const time = cols.get('time');
  if (!time || time.length < 10) throw new Error('Keine Zeitspalte ("time") oder zu wenige Frames im Log');
  const n = time.length;
  const t = new Float64Array(n);
  for (let i = 0; i < n; i++) t[i] = time[i] * units.timeToSec;

  const toF32 = (src: Float64Array | null, f: (v: number) => number) => {
    const out = new Float32Array(n);
    if (src) for (let i = 0; i < n; i++) out[i] = f(src[i]);
    return out;
  };

  const gyroCols = GYRO_NAMES.map((names) => first(cols, names));
  if (gyroCols.some((c) => !c)) throw new Error('Gyro-Spalten (gyroADC[0..2]) fehlen im Log');
  const gyro = gyroCols.map((c) => toF32(c, (v) => v * units.gyroToDeg)) as Vec3Arr;

  const accCols = ACC_NAMES.map((names) => first(cols, names));
  const acc = accCols.every((c) => c) ? (accCols.map((c) => toF32(c, (v) => v)) as Vec3Arr) : null;

  // Throttle: rcCommand[3] is 1000..2000 on Betaflight, setpoint[3] is 0..1000
  const rcThr = cols.get('rcCommand[3]');
  const spThr = cols.get('setpoint[3]');
  let throttle: Float32Array;
  if (rcThr) {
    const med = median(rcThr, Math.max(1, Math.floor(n / 2000)));
    const lo = med > 700 ? 1000 : 0;
    throttle = toF32(rcThr, (v) => Math.min(1, Math.max(0, (v - lo) / 1000)));
  } else if (spThr) {
    throttle = toF32(spThr, (v) => Math.min(1, Math.max(0, v / 1000)));
  } else {
    throttle = new Float32Array(n);
  }

  const sticks = [0, 1, 2].map((a) => toF32(cols.get(RC_NAMES[a]) ?? null, (v) => Math.max(-1, Math.min(1, v / 500)))) as Vec3Arr;

  const motorCols = MOTOR_NAMES.map((m) => cols.get(m)).filter((c): c is Float64Array => !!c);
  let motor: Float32Array | null = null;
  if (motorCols.length) {
    const [lo, hi] = motorRange(headers);
    const span = hi - lo;
    motor = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let s = 0;
      for (const c of motorCols) s += c[i];
      motor[i] = Math.min(1, Math.max(0, (s / motorCols.length - lo) / span));
    }
  }

  const vbatStart = startVoltage(t, cols.get('vbatLatest'), units.vbatToVolt);
  return { t, gyro, acc, accScale: units.accScale, throttle, sticks, motor, vbatStart };
}

/** Gyro scale for .bbl logs. Betaflight logs deg/s directly (gyro_scale = 1.0f). */
export function gyroToDegFromHeaders(headers: Record<string, string>): number {
  const fw = `${headers['Firmware revision'] ?? ''} ${headers['Firmware type'] ?? ''}`;
  if (/betaflight|emuflight|rotorflight/i.test(fw)) return 1;
  const hex = headers['gyro_scale'];
  if (!hex) return 1;
  const bits = parseInt(hex, 16);
  const f = new Float32Array(new Uint32Array([bits]).buffer)[0];
  if (!Number.isFinite(f) || f <= 0 || Math.abs(f - 1) < 1e-6) return 1;
  // Cleanflight / INAV: radians per microsecond per LSB
  return (f * 1e6 * 180) / Math.PI;
}
