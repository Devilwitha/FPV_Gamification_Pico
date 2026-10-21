// Synthetic freestyle flight so every feature can be tried without a real log.
import { pt1ZeroPhase } from '../analysis/filters.ts';
import { buildFlightData } from '../pipeline/process.ts';
import type { FlightData, RawSeries, Vec3Arr } from '../types.ts';

interface Pulse {
  axis: 0 | 1 | 2;
  angle: number;
  start: number;
  dur: number;
}
interface ThrottleWindow {
  t0: number;
  t1: number;
  v: number;
}

const RATE = 500;
const DURATION = 64;

// deterministic PRNG so the demo looks the same every time
function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

export function simulateFlight(): FlightData {
  const rand = rng(42);
  const gauss = () => Math.sqrt(-2 * Math.log(rand() + 1e-12)) * Math.cos(2 * Math.PI * rand());
  const pulses: Pulse[] = [];
  const thr: ThrottleWindow[] = [];
  const P = (axis: 0 | 1 | 2, angle: number, start: number, dur: number) => pulses.push({ axis, angle, start, dur });
  const T = (t0: number, t1: number, v: number) => thr.push({ t0, t1, v });

  P(1, 25, 4.0, 1.0); // tilt forward into cruise
  P(0, 360, 7.0, 0.5); // roll
  P(0, 720, 10.0, 0.9); // double roll
  P(1, 360, 13.5, 0.55); T(13.4, 14.05, 0.06); T(14.1, 14.5, 0.9); // front flip + catch
  P(1, -360, 17.0, 1.7); T(16.8, 18.8, 0.85); // power loop
  P(0, 180, 21.5, 0.35); P(1, -180, 21.95, 0.6); T(21.4, 22.6, 0.22); T(22.6, 23.0, 0.8); // split-S
  P(2, 360, 25.0, 0.6); // yaw spin
  P(0, -360, 28.0, 0.5); P(1, -360, 28.9, 0.55); T(28.85, 29.5, 0.07); T(29.5, 29.9, 0.9); // roll → back flip combo
  P(1, -180, 32.5, 0.7); P(0, 180, 33.3, 0.35); T(32.4, 33.2, 0.7); // Immelmann
  P(0, 1080, 36.0, 1.3); // triple roll
  P(0, 360, 40.0, 1.0); P(1, -360, 40.0, 1.0); T(39.9, 41.0, 0.5); // corkscrew
  P(0, 25, 43.6, 0.4); P(0, -25, 48.0, 0.4); // bank for orbit
  P(1, -360, 50.0, 1.8); T(49.8, 51.9, 0.88); // power loop
  P(0, 360, 52.3, 0.5); // roll right after (combo)
  P(1, -170, 55.0, 0.6); T(54.9, 55.7, 0.15); // half flip only → candidate, no trick
  P(1, 170, 56.2, 0.6);
  P(1, -25, 58.0, 1.0); // level out

  const n = DURATION * RATE;
  const t = new Float64Array(n);
  const gyro = [new Float32Array(n), new Float32Array(n), new Float32Array(n)] as Vec3Arr;
  const throttleRaw = new Float32Array(n);

  for (let i = 0; i < n; i++) {
    const ti = i / RATE;
    t[i] = ti;
    const airborne = ti > 3 && ti < 60.5;
    // gentle carving: sinusoids integrate to bounded angles
    if (airborne) {
      gyro[0][i] = 35 * Math.sin(ti * 0.9) + 18 * Math.sin(ti * 2.3 + 1);
      gyro[1][i] = 20 * Math.sin(ti * 0.7 + 2) + 10 * Math.sin(ti * 3.1);
      gyro[2][i] = 30 * Math.sin(ti * 0.5 + 0.5);
      if (ti > 44 && ti < 48) gyro[2][i] += 90; // orbit
    }
    for (const p of pulses) {
      if (ti >= p.start && ti <= p.start + p.dur) {
        // raised-cosine rate profile, integrates exactly to p.angle
        gyro[p.axis][i] += (p.angle / p.dur) * (1 - Math.cos((2 * Math.PI * (ti - p.start)) / p.dur));
      }
    }
    let th = 0;
    if (ti >= 1.0 && ti < 2.0) th = 0.02;
    else if (ti >= 2.0 && ti < 3.5) th = 0.02 + ((ti - 2) / 1.5) * 0.5;
    else if (ti >= 3.5 && ti < 59) th = 0.46 + 0.07 * Math.sin(ti * 1.3) + 0.04 * Math.sin(ti * 4.1);
    else if (ti >= 59 && ti < 61) th = 0.46 * (1 - (ti - 59) / 2);
    for (const w of thr) if (ti >= w.t0 && ti <= w.t1) th = w.v;
    throttleRaw[i] = th;
  }
  const throttle = pt1ZeroPhase(throttleRaw, RATE, 5);
  // sensor noise + motor-induced vibration
  for (let i = 0; i < n; i++) {
    const vib = 3 + 6 * throttle[i];
    for (let a = 0; a < 3; a++) gyro[a][i] += gauss() * vib;
  }
  const sticks = [0, 1, 2].map((a) => {
    const s = pt1ZeroPhase(gyro[a], RATE, 8);
    return s.map((v) => Math.max(-1, Math.min(1, v / 800)));
  }) as Vec3Arr;
  const motor = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const effort = (Math.abs(gyro[0][i]) + Math.abs(gyro[1][i]) + Math.abs(gyro[2][i])) / 6000;
    motor[i] = t[i] < 1 ? 0 : Math.min(1, 0.04 + 0.9 * throttle[i] + effort);
  }

  const raw: RawSeries = { t, gyro, acc: null, accScale: 1, throttle, sticks, motor };
  return buildFlightData(
    raw,
    {
      source: 'demo',
      fileName: 'demo-flight.bbl',
      firmware: 'Demo (synthetischer Flug)',
      craft: 'Demo-Quad',
      fields: ['time', 'gyroADC[0]', 'gyroADC[1]', 'gyroADC[2]', 'rcCommand[0]', 'rcCommand[1]', 'rcCommand[2]', 'rcCommand[3]', 'motor[0]'],
      headers: { Product: 'Blackbox Arena Demo', 'Firmware revision': 'Demo', 'Craft name': 'Demo-Quad', looptime: '125' },
      logIndex: 0,
      logCount: 1,
      corruptFrames: 0,
    },
    [
      { type: 'sync_beep', time: 1.0 },
      { type: 'disarm', time: 62.0, value: 1 },
    ],
  );
}
