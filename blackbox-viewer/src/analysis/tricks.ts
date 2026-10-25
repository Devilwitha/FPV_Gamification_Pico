// Phase 2: trick detection on body rates + scoring.
//
// 1. Low-pass the gyro (zero phase) and integrate raw rates to cumulative body angles.
// 2. Per axis, find "rotation runs": intervals where the rate keeps one sign above `endRate`
//    and peaks above `startRate`. The integrated angle of a run says how far the quad rotated.
// 3. Full rotations (≥ 360° minus tolerance) → Roll / Flip / Power Loop / Matty Flip / Yaw Spin,
//    half rotations (~180°) are paired in time → Split-S (roll then pitch) or Immelmann (pitch then roll).
//    Simultaneous roll + flip → Corkscrew, simultaneous roll + yaw → Helix.
//    Slow loop with throttle: pulled back → Power Loop, pushed forward → Matty Flip.
// 4. With a trustworthy attitude (accelerometer logged): Yaw Spin while inverted → Trippy Spin,
//    holding inverted → Inverted Hang, nose down with low throttle → Dive. A slow 360° yaw → Orbit.
// 5. Every candidate gets a feature vector (used by the optional kNN classifier) and points.
// 6. Gaps are searched with the community model (learned.ts): approved marked examples, including
//    community tricks ("c<id>") that have no rule at all.
import { cumulative, pt1ZeroPhase } from './filters.ts';
import { findLearnedTricks, type LabelExample } from './learned.ts';
import { rotationAt } from './orientation.ts';
import type { BuiltinTrickType, FlightData, Trick, TrickType, Vec3Arr } from '../types.ts';

export interface DetectSettings {
  startRate: number;
  endRate: number;
  smoothHz: number;
  fullTol: number;
  halfMin: number;
  halfMax: number;
  maxSpinTime: number;
  plMinDuration: number;
  plMinThrottle: number;
  yawMinPeak: number;
  seqGap: number;
  comboWindow: number;
  repeatGap: number;
  hangMinTime: number;
  diveMinTime: number;
  diveMaxThrottle: number;
  orbitMinTime: number;
}

export const DEFAULT_SETTINGS: DetectSettings = {
  startRate: 300,
  endRate: 60,
  smoothHz: 15,
  fullTol: 0.15,
  halfMin: 130,
  halfMax: 235,
  maxSpinTime: 1.3,
  plMinDuration: 0.9,
  plMinThrottle: 0.4,
  yawMinPeak: 450,
  seqGap: 0.6,
  comboWindow: 1.5,
  repeatGap: 5,
  hangMinTime: 0.8,
  diveMinTime: 0.7,
  diveMaxThrottle: 0.3,
  orbitMinTime: 2,
};

export const TRICK_TYPES: BuiltinTrickType[] = [
  'roll', 'flip', 'powerloop', 'mattyflip', 'splits', 'immelmann', 'yawspin', 'trippyspin',
  'corkscrew', 'helix', 'orbit', 'invertedhang', 'dive', 'none',
];

export const TRICK_LABEL: Record<string, string> = {
  roll: 'Roll',
  flip: 'Flip',
  powerloop: 'Power Loop',
  splits: 'Split-S',
  immelmann: 'Immelmann',
  yawspin: 'Yaw Spin',
  corkscrew: 'Corkscrew',
  mattyflip: 'Matty Flip',
  helix: 'Helix',
  trippyspin: 'Trippy Spin',
  orbit: 'Orbit',
  invertedhang: 'Inverted Hang',
  dive: 'Dive',
  none: 'Kein Trick',
};

export const TRICK_COLOR: Record<string, string> = {
  roll: '#ff4d6d',
  flip: '#3ddc97',
  powerloop: '#ffb703',
  splits: '#4cc9f0',
  immelmann: '#9b5de5',
  yawspin: '#f15bb5',
  corkscrew: '#fee440',
  mattyflip: '#fb8500',
  helix: '#80ffdb',
  trippyspin: '#c77dff',
  orbit: '#90be6d',
  invertedhang: '#4361ee',
  dive: '#e63946',
  none: '#6b7280',
};

const BASE_POINTS: Record<string, number> = {
  roll: 100,
  flip: 120,
  powerloop: 250,
  splits: 200,
  immelmann: 200,
  yawspin: 80,
  corkscrew: 320,
  mattyflip: 260,
  helix: 300,
  trippyspin: 220,
  orbit: 60,
  invertedhang: 140,
  dive: 160,
  none: 0,
};

/** Community trick approved on the website (key "c<id>"); `pending` = own request, not yet approved. */
export interface CustomTrick {
  key: string;
  name: string;
  points: number;
  description?: string;
  pending?: boolean;
}

let customTricks: CustomTrick[] = [];

export const isBuiltin = (type: TrickType): type is BuiltinTrickType => (TRICK_TYPES as string[]).includes(type);

/** Stable colour per community trick (hex, the timeline appends an alpha suffix). */
function customColor(key: string): string {
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const hue = (h * 137) % 360;
  const f = (n: number) => {
    const k = (n + hue / 30) % 12;
    const c = 0.65 - 0.35 * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(c * 255).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** Replace the known community tricks (names, colours, points). */
export function registerCustomTricks(list: CustomTrick[]) {
  for (const c of customTricks) {
    delete TRICK_LABEL[c.key];
    delete TRICK_COLOR[c.key];
    delete BASE_POINTS[c.key];
  }
  customTricks = list.filter((c) => !isBuiltin(c.key));
  for (const c of customTricks) {
    TRICK_LABEL[c.key] = c.name;
    TRICK_COLOR[c.key] = customColor(c.key);
    BASE_POINTS[c.key] = c.points;
  }
}

export const getCustomTricks = () => customTricks;

/** Tricks without a rotation goal – the HUD shows elapsed time instead of degrees. */
export const HOLD_TRICKS: TrickType[] = ['invertedhang', 'dive'];

interface Run {
  axis: 0 | 1 | 2;
  sign: number;
  i0: number;
  i1: number;
  angle: number;
  peak: number;
  used: boolean;
}

interface Candidate {
  i0: number;
  i1: number;
  type: TrickType;
  count: number;
  axis: 0 | 1 | 2;
  sign: number;
  /** found by the community model */
  learned?: boolean;
}

export interface Analysis {
  tricks: Trick[];
  /** candidates that ended up as "none" (rejected by rules/ML/user) */
  rejected: Trick[];
  cum: [Float64Array, Float64Array, Float64Array];
  smooth: Vec3Arr;
}

export type Labeler = (features: number[]) => { label: TrickType; confidence: number } | null;

export function trickName(type: TrickType, count: number): string {
  const base = TRICK_LABEL[type] ?? type;
  if (count <= 1 || type === 'none') return base;
  const prefix = count === 2 ? 'Double' : count === 3 ? 'Triple' : count === 4 ? 'Quad' : `${count}×`;
  return `${prefix} ${base}`;
}

function findRuns(smooth: Vec3Arr, cum: Float64Array[], s: DetectSettings): Run[] {
  const runs: Run[] = [];
  for (const axis of [0, 1, 2] as const) {
    const r = smooth[axis];
    const n = r.length;
    let i = 0;
    while (i < n) {
      if (Math.abs(r[i]) <= s.endRate) {
        i++;
        continue;
      }
      const sign = Math.sign(r[i]);
      let j = i;
      let peak = 0;
      while (j < n && r[j] * sign > s.endRate) {
        peak = Math.max(peak, Math.abs(r[j]));
        j++;
      }
      if (peak >= s.startRate) runs.push({ axis, sign, i0: i, i1: j - 1, angle: cum[axis][j - 1] - cum[axis][i], peak, used: false });
      i = j;
    }
  }
  return runs.sort((a, b) => a.i0 - b.i0);
}

function meanOver(x: Float32Array, i0: number, i1: number): number {
  let s = 0;
  for (let i = i0; i <= i1; i++) s += x[i];
  return s / Math.max(1, i1 - i0 + 1);
}

/** Feature vector for the kNN classifier – computed identically for every candidate span. */
export function spanFeatures(f: FlightData, a: Pick<Analysis, 'cum' | 'smooth'>, i0: number, i1: number) {
  const angles = [0, 1, 2].map((ax) => a.cum[ax][i1] - a.cum[ax][i0]) as [number, number, number];
  const duration = f.t[i1] - f.t[i0];
  const meanThrottle = meanOver(f.throttle, i0, i1);
  let peak = 0;
  let wR = 0, wP = 0, cR = 0, cP = 0;
  for (let i = i0; i <= i1; i++) {
    const r = Math.abs(a.smooth[0][i]), p = Math.abs(a.smooth[1][i]), y = Math.abs(a.smooth[2][i]);
    peak = Math.max(peak, r, p, y);
    wR += r; cR += r * f.t[i];
    wP += p; cP += p * f.t[i];
  }
  // >0: roll happens before pitch (Split-S), <0: pitch before roll (Immelmann)
  const order = wR > 0 && wP > 0 && duration > 0 ? Math.max(-1, Math.min(1, (cP / wP - cR / wR) / duration)) : 0;
  const features = [Math.abs(angles[0]) / 360, Math.abs(angles[1]) / 360, Math.abs(angles[2]) / 360, duration, meanThrottle, peak / 1000, order];
  return { angles, duration, meanThrottle, peak, features };
}

const COUNTED: TrickType[] = ['roll', 'flip', 'yawspin', 'trippyspin', 'corkscrew', 'helix', 'orbit'];

function countFor(type: TrickType, angles: number[]): number {
  const axis = axisFor(type, 0);
  if (COUNTED.includes(type)) return Math.max(1, Math.round(Math.abs(angles[axis]) / 360));
  return 1;
}

function axisFor(type: TrickType, fallback: 0 | 1 | 2): 0 | 1 | 2 {
  if (type === 'roll' || type === 'corkscrew' || type === 'helix' || type === 'invertedhang') return 0;
  if (type === 'flip' || type === 'powerloop' || type === 'mattyflip' || type === 'dive') return 1;
  if (type === 'yawspin' || type === 'trippyspin' || type === 'orbit') return 2;
  return fallback;
}

/** World-up expressed in the body z axis: 1 = level, -1 = upside down. */
function upZ(quat: Float32Array, i: number): number {
  const w = quat[i * 4], x = quat[i * 4 + 1], y = quat[i * 4 + 2], z = quat[i * 4 + 3];
  return w * w - x * x - y * y + z * z;
}

/** Spans (i0, i1) where `test(i)` holds continuously for at least `minTime` seconds. */
function holdSpans(f: FlightData, minTime: number, test: (i: number) => boolean): [number, number][] {
  const spans: [number, number][] = [];
  const n = f.t.length;
  let i = 0;
  while (i < n) {
    if (!test(i)) {
      i++;
      continue;
    }
    let j = i;
    while (j + 1 < n && test(j + 1)) j++;
    if (f.t[j] - f.t[i] >= minTime) spans.push([i, j]);
    i = j + 1;
  }
  return spans;
}

/** Share of [i0, i1] covered by any of the given candidates (0..1). */
function coveredBy(f: FlightData, i0: number, i1: number, cands: Candidate[]): number {
  const len = f.t[i1] - f.t[i0] || 1e-6;
  let covered = 0;
  for (const c of cands) {
    if (c.type === 'none') continue;
    covered += Math.max(0, Math.min(f.t[i1], f.t[c.i1]) - Math.max(f.t[i0], f.t[c.i0]));
  }
  return Math.min(1, covered / len);
}

export function detectTricks(
  f: FlightData,
  s: DetectSettings,
  opts: { labeler?: Labeler | null; userLabels?: Map<string, TrickType>; learned?: LabelExample[] | null } = {},
): Analysis {
  const smooth = f.gyro.map((g) => pt1ZeroPhase(g, f.rate, s.smoothHz)) as Vec3Arr;
  const cum = f.gyro.map((g) => cumulative(g, f.t)) as [Float64Array, Float64Array, Float64Array];
  const runs = findRuns(smooth, cum, s);
  const cands: Candidate[] = [];
  const halves: Run[] = [];

  for (const run of runs) {
    const abs = Math.abs(run.angle);
    const count = Math.floor(abs / 360 + s.fullTol);
    const dur = f.t[run.i1] - f.t[run.i0];
    if (count >= 1) {
      let type: TrickType | null = null;
      if (run.axis === 0 && dur <= s.maxSpinTime * count) type = 'roll';
      if (run.axis === 1) {
        const thr = meanOver(f.throttle, run.i0, run.i1);
        // slow loop with throttle: pulled back (nose up) = Power Loop, pushed forward = Matty Flip
        if (count === 1 && dur >= s.plMinDuration && thr >= s.plMinThrottle && dur <= 4) type = run.sign > 0 ? 'mattyflip' : 'powerloop';
        else if (dur <= s.maxSpinTime * count) type = 'flip';
      }
      if (run.axis === 2 && run.peak >= s.yawMinPeak && dur <= s.maxSpinTime * count * 1.5) type = 'yawspin';
      if (type) {
        run.used = true;
        cands.push({ i0: run.i0, i1: run.i1, type, count, axis: run.axis, sign: run.sign });
      }
    } else if (run.axis !== 2 && abs >= s.halfMin && abs <= s.halfMax) {
      halves.push(run);
    }
  }

  // Half-roll + half-loop sequences
  const center = (r: Run) => (f.t[r.i0] + f.t[r.i1]) / 2;
  for (const hr of halves) {
    if (hr.axis !== 0 || hr.used) continue;
    let best: Run | null = null;
    let bestGap = Infinity;
    for (const hp of halves) {
      if (hp.axis !== 1 || hp.used) continue;
      const first = center(hr) < center(hp) ? hr : hp;
      const second = first === hr ? hp : hr;
      const gap = f.t[second.i0] - f.t[first.i1];
      if (gap >= -0.3 && gap <= s.seqGap && Math.abs(gap) < bestGap) {
        best = hp;
        bestGap = Math.abs(gap);
      }
    }
    if (best) {
      hr.used = best.used = true;
      const rollFirst = center(hr) < center(best);
      cands.push({
        i0: Math.min(hr.i0, best.i0),
        i1: Math.max(hr.i1, best.i1),
        type: rollFirst ? 'splits' : 'immelmann',
        count: 1,
        axis: rollFirst ? 1 : 0,
        sign: 1,
      });
    }
  }

  // Simultaneous roll + flip → corkscrew
  cands.sort((a, b) => a.i0 - b.i0);
  for (const a of cands) {
    if (a.type !== 'roll') continue;
    for (const b of cands) {
      if (b === a || (b.type !== 'flip' && b.type !== 'powerloop')) continue;
      const overlap = Math.min(f.t[a.i1], f.t[b.i1]) - Math.max(f.t[a.i0], f.t[b.i0]);
      const shorter = Math.min(f.t[a.i1] - f.t[a.i0], f.t[b.i1] - f.t[b.i0]);
      if (overlap > 0.5 * shorter) {
        a.i0 = Math.min(a.i0, b.i0);
        a.i1 = Math.max(a.i1, b.i1);
        a.type = 'corkscrew';
        a.count = Math.min(a.count, b.count);
        b.type = 'none';
      }
    }
  }

  // Simultaneous roll + yaw (both ≥ one turn) → helix
  for (const a of cands) {
    if (a.type !== 'roll') continue;
    for (const yaw of runs) {
      if (yaw.axis !== 2 || Math.abs(yaw.angle) < 360 * (1 - s.fullTol)) continue;
      const overlap = Math.min(f.t[a.i1], f.t[yaw.i1]) - Math.max(f.t[a.i0], f.t[yaw.i0]);
      const shorter = Math.min(f.t[a.i1] - f.t[a.i0], f.t[yaw.i1] - f.t[yaw.i0]);
      if (overlap <= 0.5 * shorter) continue;
      for (const b of cands) if (b.type === 'yawspin' && b.i0 === yaw.i0) b.type = 'none';
      yaw.used = true;
      a.i0 = Math.min(a.i0, yaw.i0);
      a.i1 = Math.max(a.i1, yaw.i1);
      a.type = 'helix';
      a.count = Math.min(a.count, Math.max(1, Math.floor(Math.abs(yaw.angle) / 360 + s.fullTol)));
      break;
    }
  }

  // Attitude-based tricks need a trustworthy attitude: only with a logged accelerometer.
  const attitude = f.acc !== null;
  if (attitude) {
    // Yaw Spin while upside down → Trippy Spin
    for (const c of cands) {
      if (c.type !== 'yawspin') continue;
      let inverted = 0;
      for (let i = c.i0; i <= c.i1; i++) if (upZ(f.quat, i) < -0.3) inverted++;
      if (inverted >= 0.6 * (c.i1 - c.i0 + 1)) c.type = 'trippyspin';
    }
    // Nose pointing down (world z of the body x axis) – takes precedence over "inverted"
    const m = new Float64Array(9);
    const noseDown = (i: number) => rotationAt(f.quat, i, m)[6] < -0.75;
    // Holding inverted (not part of a flip/roll, nose not pointing down) → Inverted Hang
    for (const [i0, i1] of holdSpans(f, s.hangMinTime, (i) => upZ(f.quat, i) < -0.7 && !noseDown(i))) {
      if (coveredBy(f, i0, i1, cands) < 0.3) cands.push({ i0, i1, type: 'invertedhang', count: 1, axis: 0, sign: 1 });
    }
    // Nose pointing down with low throttle → Dive
    for (const [i0, i1] of holdSpans(f, s.diveMinTime, noseDown)) {
      if (meanOver(f.throttle, i0, i1) <= s.diveMaxThrottle && coveredBy(f, i0, i1, cands) < 0.3) {
        cands.push({ i0, i1, type: 'dive', count: 1, axis: 1, sign: 1 });
      }
    }
  }

  // Slow full yaw turn (below spin rate) → Orbit; banked if the attitude is known
  {
    const yaw = pt1ZeroPhase(f.gyro[2], f.rate, 1.5);
    const n = yaw.length;
    let i = 0;
    while (i < n) {
      if (Math.abs(yaw[i]) <= 35) {
        i++;
        continue;
      }
      const sign = Math.sign(yaw[i]);
      let j = i;
      let peak = 0;
      while (j < n && yaw[j] * sign > 35) {
        peak = Math.max(peak, Math.abs(yaw[j]));
        j++;
      }
      const i1 = j - 1;
      const angle = Math.abs(cum[2][i1] - cum[2][i]);
      const dur = f.t[i1] - f.t[i];
      const turns = Math.floor(angle / 360 + s.fullTol);
      let banked = true;
      if (attitude) {
        let bank = 0;
        for (let k = i; k <= i1; k++) bank += Math.abs(upZ(f.quat, k)) < 0.985 ? 1 : 0;
        banked = bank >= 0.5 * (i1 - i + 1);
      }
      if (turns >= 1 && dur >= s.orbitMinTime && dur <= 20 * turns && peak < s.yawMinPeak && banked && coveredBy(f, i, i1, cands) < 0.3) {
        cands.push({ i0: i, i1, type: 'orbit', count: turns, axis: 2, sign });
      }
      i = j;
    }
  }

  const final = cands.filter((c) => c.type !== 'none');
  // Community model: approved examples fill the gaps the rules left
  const learned: [number, number][] = [];
  if (opts.learned?.length) {
    const known = new Set([...TRICK_TYPES, ...customTricks.map((c) => c.key)] as string[]);
    const examples = opts.learned.filter((e) => known.has(e.label));
    for (const h of findLearnedTricks(f, cum, smooth, examples, final.map((c): [number, number] => [c.i0, c.i1]))) {
      const angles = [0, 1, 2].map((ax) => cum[ax][h.i1] - cum[ax][h.i0]);
      const dominant = angles.reduce((best, v, ax) => (Math.abs(v) > Math.abs(angles[best]) ? ax : best), 0) as 0 | 1 | 2;
      final.push({ i0: h.i0, i1: h.i1, type: h.label, count: countFor(h.label, angles), axis: dominant, sign: 1, learned: true });
      learned.push([h.i0, h.i1]);
    }
  }
  // Leftover sizeable rotations become "none" candidates so the user/ML can label them
  const inLearned = (run: Run) => learned.some(([a, b]) => Math.min(run.i1, b) - Math.max(run.i0, a) > 0.5 * (run.i1 - run.i0));
  for (const run of runs) {
    if (!run.used && Math.abs(run.angle) >= 120 && !inLearned(run)) {
      final.push({ i0: run.i0, i1: run.i1, type: 'none', count: 0, axis: run.axis, sign: run.sign });
    }
  }
  final.sort((a, b) => a.i0 - b.i0);

  const analysis: Analysis = { tricks: [], rejected: [], cum, smooth };
  let id = 0;
  for (const c of final) {
    const { angles, duration, meanThrottle, peak, features } = spanFeatures(f, analysis, c.i0, c.i1);
    const key = f.t[c.i0].toFixed(2);
    let type = c.type;
    let source: Trick['source'] = c.learned ? 'community' : 'rules';
    let count = c.count;
    const ml = c.learned ? null : opts.labeler?.(features);
    if (ml && ml.confidence >= 0.6 && ml.label !== type) {
      type = ml.label;
      source = 'ml';
      count = countFor(type, angles);
    }
    const user = opts.userLabels?.get(key);
    if (user && user !== type) {
      type = user;
      source = 'user';
      count = countFor(type, angles);
    } else if (user) {
      source = 'user';
    }
    const axis = axisFor(type, c.axis);
    const dominant = Math.abs(angles[axis]) || 1;
    const purity =
      type === 'splits' || type === 'immelmann' || type === 'corkscrew'
        ? Math.max(0, 1 - Math.abs(angles[2]) / (Math.abs(angles[0]) + Math.abs(angles[1]) || 1))
        : type === 'helix'
          ? Math.max(0, 1 - Math.abs(angles[1]) / (Math.abs(angles[0]) + Math.abs(angles[2]) || 1))
          : type === 'orbit' || HOLD_TRICKS.includes(type) || !isBuiltin(type)
            ? 1
            : Math.max(0, Math.min(1, 1 - (Math.abs(angles[0]) + Math.abs(angles[1]) + Math.abs(angles[2]) - dominant) / dominant));
    const dirSign = Math.sign(angles[axis]);
    const dir =
      type === 'roll' ? (dirSign > 0 ? 'rechts' : 'links')
      : type === 'flip' ? (dirSign > 0 ? 'vorwärts' : 'rückwärts')
      : '';
    const trick: Trick = {
      id: id++,
      key,
      type,
      name: trickName(type, count),
      detail: [dir, `${duration.toFixed(2)} s`, `${Math.round(peak)}°/s`].filter(Boolean).join(' · '),
      count,
      start: f.t[c.i0],
      end: f.t[c.i1],
      angles,
      duration,
      meanThrottle,
      peakRate: peak,
      purity,
      basePoints: 0,
      comboLevel: 0,
      comboMult: 1,
      repeat: 0,
      repeatMult: 1,
      points: 0,
      source,
      features,
      axis,
    };
    (type === 'none' ? analysis.rejected : analysis.tricks).push(trick);
  }
  scoreTricks(analysis.tricks, s);
  return analysis;
}

/** Repeats score less: the same trick directly again gives half the points, then a quarter, … */
export const REPEAT_FACTOR = 0.5;
export const REPEAT_MIN = 0.125;
/** At most this many identical tricks in a row count for one combo; the next one starts a new combo. */
export const MAX_SAME_IN_COMBO = 2;

export function scoreTricks(tricks: Trick[], s: DetectSettings) {
  let combo = 0;
  let lastEnd = -Infinity;
  let prev: Trick | null = null;
  for (const t of tricks) {
    // same trick (type) directly after the previous one, without another trick in between
    t.repeat = prev && prev.type === t.type && t.start - prev.end <= s.repeatGap ? prev.repeat + 1 : 0;
    t.repeatMult = Math.max(REPEAT_MIN, REPEAT_FACTOR ** t.repeat);
    const countMult = 1 + 0.8 * (t.count - 1);
    let style = 1;
    if (t.type === 'roll' || t.type === 'flip' || t.type === 'yawspin' || t.type === 'trippyspin' || t.type === 'corkscrew' || t.type === 'helix') {
      style = Math.min(1.5, Math.max(0.7, t.peakRate / 800));
    } else if (t.type === 'powerloop' || t.type === 'mattyflip') {
      style = 1 + 0.5 * t.meanThrottle;
    } else if (HOLD_TRICKS.includes(t.type)) {
      // longer hang / dive = more points (up to 1.5×)
      style = Math.min(1.5, Math.max(1, t.duration / 1.2));
    }
    const clean = 0.6 + 0.4 * t.purity;
    t.basePoints = Math.round(((BASE_POINTS[t.type] ?? 0) * countMult * style * clean * t.repeatMult) / 5) * 5;
    combo = t.start - lastEnd <= s.comboWindow && t.repeat < MAX_SAME_IN_COMBO ? combo + 1 : 0;
    t.comboLevel = combo;
    t.comboMult = Math.min(3, 1 + 0.25 * combo);
    t.points = Math.round(t.basePoints * t.comboMult);
    lastEnd = t.end;
    prev = t;
  }
}

export interface ScoreSummary {
  total: number;
  count: number;
  best: Trick | null;
  maxCombo: number;
  byType: Partial<Record<TrickType, number>>;
}

export function summarize(tricks: Trick[], until = Infinity): ScoreSummary {
  let total = 0;
  let best: Trick | null = null;
  let maxCombo = 0;
  let count = 0;
  const byType: Partial<Record<TrickType, number>> = {};
  for (const t of tricks) {
    if (t.end > until) continue;
    total += t.points;
    count++;
    if (!best || t.points > best.points) best = t;
    maxCombo = Math.max(maxCombo, t.comboLevel + 1);
    byType[t.type] = (byType[t.type] ?? 0) + 1;
  }
  return { total, count, best, maxCombo, byType };
}
