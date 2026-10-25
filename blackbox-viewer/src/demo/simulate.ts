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
const DURATION = 92;

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
  P(0, 180, 63.0, 0.35); P(0, -180, 64.6, 0.35); // half roll, hang upside down, roll back → Inverted Hang
  P(0, 180, 67.5, 0.35); P(2, 360, 68.0, 0.6); T(67.9, 68.7, 0.06); P(0, -180, 68.9, 0.35); // inverted yaw spin → Trippy Spin
  P(1, 90, 72.0, 0.4); T(72.0, 73.5, 0.08); P(1, -90, 73.4, 0.5); // nose down, throttle off → Dive
  P(1, 360, 76.5, 1.7); T(76.3, 78.3, 0.85); // pushed forward loop with throttle → Matty Flip
  P(0, 360, 81.0, 0.8); P(2, 360, 81.0, 0.8); // roll + yaw together → Helix
  P(1, -25, 84.0, 1.0); P(1, 25, 86.0, 1.0); // settle
  // the "pilot" levels the quad between maneuvers – except while banking or hanging on purpose
  const holds = [[43.5, 48.5], [63.0, 65.0], [67.5, 69.3], [72.0, 74.0]];

  const n = DURATION * RATE;
  const t = new Float64Array(n);
  const gyro = [new Float32Array(n), new Float32Array(n), new Float32Array(n)] as Vec3Arr;
  const throttleRaw = new Float32Array(n);
  // true attitude (body→world quaternion) → accelerometer = gravity seen in the body frame
  const acc = [new Float32Array(n), new Float32Array(n), new Float32Array(n)] as Vec3Arr;
  let q0 = 1, q1 = 0, q2 = 0, q3 = 0;
  const DEG = Math.PI / 180;

  for (let i = 0; i < n; i++) {
    const ti = i / RATE;
    t[i] = ti;
    const airborne = ti > 3 && ti < 88.5;
    // gentle carving: sinusoids integrate to bounded angles
    if (airborne) {
      gyro[0][i] = 35 * Math.sin(ti * 0.9) + 18 * Math.sin(ti * 2.3 + 1);
      gyro[1][i] = 20 * Math.sin(ti * 0.7 + 2) + 10 * Math.sin(ti * 3.1);
      gyro[2][i] = 30 * Math.sin(ti * 0.5 + 0.5);
      if (ti > 44 && ti < 48) gyro[2][i] += 105; // orbit
    }
    let maneuver = holds.some(([a, b]) => ti >= a && ti <= b);
    for (const p of pulses) {
      if (ti >= p.start && ti <= p.start + p.dur) {
        // raised-cosine rate profile, integrates exactly to p.angle
        gyro[p.axis][i] += (p.angle / p.dur) * (1 - Math.cos((2 * Math.PI * (ti - p.start)) / p.dur));
        maneuver = true;
      }
    }
    // world-up in the body frame
    const ux = 2 * (q1 * q3 - q0 * q2), uy = 2 * (q0 * q1 + q2 * q3), uz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;
    acc[0][i] = ux; acc[1][i] = uy; acc[2][i] = uz;
    if (airborne && !maneuver) {
      // leveling: rotate the body so that world-up moves toward body z (≤ ~50°/s)
      gyro[0][i] += -0.9 * uy / DEG;
      gyro[1][i] += 0.9 * ux / DEG;
    }
    const h = 0.5 / RATE, gx = gyro[0][i] * DEG, gy = gyro[1][i] * DEG, gz = gyro[2][i] * DEG;
    const a0 = q0, a1 = q1, a2 = q2;
    q0 += (-a1 * gx - a2 * gy - q3 * gz) * h;
    q1 += (a0 * gx + a2 * gz - q3 * gy) * h;
    q2 += (a0 * gy - a1 * gz + q3 * gx) * h;
    q3 += (a0 * gz + a1 * gy - a2 * gx) * h;
    const norm = 1 / Math.hypot(q0, q1, q2, q3);
    q0 *= norm; q1 *= norm; q2 *= norm; q3 *= norm;
    let th = 0;
    if (ti >= 1.0 && ti < 2.0) th = 0.02;
    else if (ti >= 2.0 && ti < 3.5) th = 0.02 + ((ti - 2) / 1.5) * 0.5;
    else if (ti >= 3.5 && ti < 87) th = 0.46 + 0.07 * Math.sin(ti * 1.3) + 0.04 * Math.sin(ti * 4.1);
    else if (ti >= 87 && ti < 89) th = 0.46 * (1 - (ti - 87) / 2);
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

  // fully charged 4S pack
  const raw: RawSeries = { t, gyro, acc, accScale: 1, throttle, sticks, motor, vbatStart: 16.6 };
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
      { type: 'disarm', time: 90.0, value: 1 },
    ],
  );
}
