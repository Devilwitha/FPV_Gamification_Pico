// RawSeries → FlightData: monotonic time, decimation to ~500 Hz, acc scaling, attitude.
import { computeOrientation } from '../analysis/orientation.ts';
import { estimateCells } from './columns.ts';
import type { FlightData, LogEvent, LogInfo, RawSeries, Vec3Arr } from '../types.ts';

export const TARGET_RATE = 500;

export function buildFlightData(
  raw: RawSeries,
  info: Omit<LogInfo, 'srcRate' | 'duration' | 'frames' | 'vbat' | 'cells'>,
  events: LogEvent[],
  t0Override?: number,
): FlightData {
  const N = raw.t.length;
  // keep strictly increasing timestamps only
  const keep: number[] = [];
  let last = -Infinity;
  for (let i = 0; i < N; i++) {
    if (raw.t[i] > last) {
      keep.push(i);
      last = raw.t[i];
    }
  }
  const m = keep.length;
  if (m < 10) throw new Error('Zu wenige gültige Frames');

  const diffs: number[] = [];
  const step = Math.max(1, Math.floor(m / 5000));
  for (let k = step; k < m; k += step) diffs.push(raw.t[keep[k]] - raw.t[keep[k - 1]]);
  diffs.sort((a, b) => a - b);
  const dt = diffs[diffs.length >> 1] || 1 / TARGET_RATE;
  const srcRate = 1 / dt;
  const dec = Math.max(1, Math.round(srcRate / TARGET_RATE));
  const n = Math.floor(m / dec);
  const t0 = t0Override ?? raw.t[keep[0]];

  const t = new Float64Array(n);
  const avg = (src: Float32Array) => {
    const out = new Float32Array(n);
    for (let j = 0; j < n; j++) {
      let s = 0;
      for (let k = 0; k < dec; k++) s += src[keep[j * dec + k]];
      out[j] = s / dec;
    }
    return out;
  };
  for (let j = 0; j < n; j++) t[j] = raw.t[keep[j * dec]] - t0;

  const gyro = raw.gyro.map(avg) as Vec3Arr;
  let acc = raw.acc ? (raw.acc.map(avg) as Vec3Arr) : null;
  if (acc) {
    let scale = raw.accScale;
    if (!scale) {
      // auto: on average the accelerometer reads ~1 g
      const mags: number[] = [];
      for (let j = 0; j < n; j += Math.max(1, Math.floor(n / 4000))) mags.push(Math.hypot(acc[0][j], acc[1][j], acc[2][j]));
      mags.sort((a, b) => a - b);
      scale = mags[mags.length >> 1] || 1;
    }
    const s = scale;
    acc = acc.map((a) => a.map((v) => v / s)) as Vec3Arr;
    // all-zero acc columns (acc disabled) → ignore
    if (acc[2].every((v) => v === 0)) acc = null;
  }

  const flight: FlightData = {
    t,
    rate: srcRate / dec,
    gyro,
    acc,
    throttle: avg(raw.throttle),
    sticks: raw.sticks.map(avg) as Vec3Arr,
    motor: raw.motor ? avg(raw.motor) : null,
    quat: computeOrientation(t, gyro, acc),
    events: events.filter((e) => e.time >= -1 && e.time <= t[n - 1] + 1),
    info: { ...info, srcRate, duration: t[n - 1], frames: m, vbat: raw.vbatStart, cells: estimateCells(raw.vbatStart) },
  };
  return flight;
}
