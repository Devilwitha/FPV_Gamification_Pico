// Phase 2: trick detection on body rates + scoring.
//
// 1. Low-pass the gyro (zero phase) and integrate raw rates to cumulative body angles.
// 2. Per axis, find "rotation runs": intervals where the rate keeps one sign above `endRate`
//    and peaks above `startRate`. The integrated angle of a run says how far the quad rotated.
// 3. Full rotations (≥ 360° minus tolerance) → Roll / Flip / Power Loop / Yaw Spin,
//    half rotations (~180°) are paired in time → Split-S (roll then pitch) or Immelmann (pitch then roll).
//    Simultaneous roll + flip → Corkscrew.
// 4. Every candidate gets a feature vector (used by the optional kNN classifier) and points.
import { cumulative, pt1ZeroPhase } from './filters.ts';
import type { FlightData, Trick, TrickType, Vec3Arr } from '../types.ts';

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
};

export const TRICK_TYPES: TrickType[] = ['roll', 'flip', 'powerloop', 'splits', 'immelmann', 'yawspin', 'corkscrew', 'none'];

export const TRICK_LABEL: Record<TrickType, string> = {
  roll: 'Roll',
  flip: 'Flip',
  powerloop: 'Power Loop',
  splits: 'Split-S',
  immelmann: 'Immelmann',
  yawspin: 'Yaw Spin',
  corkscrew: 'Corkscrew',
  none: 'Kein Trick',
};

export const TRICK_COLOR: Record<TrickType, string> = {
  roll: '#ff4d6d',
  flip: '#3ddc97',
  powerloop: '#ffb703',
  splits: '#4cc9f0',
  immelmann: '#9b5de5',
  yawspin: '#f15bb5',
  corkscrew: '#fee440',
  none: '#6b7280',
};

const BASE_POINTS: Record<TrickType, number> = {
  roll: 100,
  flip: 120,
  powerloop: 250,
  splits: 200,
  immelmann: 200,
  yawspin: 80,
  corkscrew: 320,
  none: 0,
};

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
  const base = TRICK_LABEL[type];
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

function countFor(type: TrickType, angles: number[]): number {
  const axis = type === 'yawspin' ? 2 : type === 'flip' || type === 'powerloop' ? 1 : 0;
  if (type === 'roll' || type === 'flip' || type === 'yawspin' || type === 'corkscrew') return Math.max(1, Math.round(Math.abs(angles[axis]) / 360));
  return 1;
}

function axisFor(type: TrickType, fallback: 0 | 1 | 2): 0 | 1 | 2 {
  if (type === 'roll' || type === 'corkscrew') return 0;
  if (type === 'flip' || type === 'powerloop') return 1;
  if (type === 'yawspin') return 2;
  return fallback;
}

export function detectTricks(
  f: FlightData,
  s: DetectSettings,
  opts: { labeler?: Labeler | null; userLabels?: Map<string, TrickType> } = {},
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
        if (count === 1 && dur >= s.plMinDuration && thr >= s.plMinThrottle && dur <= 4) type = 'powerloop';
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

  const final = cands.filter((c) => c.type !== 'none');
  // Leftover sizeable rotations become "none" candidates so the user/ML can label them
  for (const run of runs) {
    if (!run.used && Math.abs(run.angle) >= 120) final.push({ i0: run.i0, i1: run.i1, type: 'none', count: 0, axis: run.axis, sign: run.sign });
  }
  final.sort((a, b) => a.i0 - b.i0);

  const analysis: Analysis = { tricks: [], rejected: [], cum, smooth };
  let id = 0;
  for (const c of final) {
    const { angles, duration, meanThrottle, peak, features } = spanFeatures(f, analysis, c.i0, c.i1);
    const key = f.t[c.i0].toFixed(2);
    let type = c.type;
    let source: Trick['source'] = 'rules';
    let count = c.count;
    const ml = opts.labeler?.(features);
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

export function scoreTricks(tricks: Trick[], s: DetectSettings) {
  let combo = 0;
  let lastEnd = -Infinity;
  for (const t of tricks) {
    const countMult = 1 + 0.8 * (t.count - 1);
    let style = 1;
    if (t.type === 'roll' || t.type === 'flip' || t.type === 'yawspin' || t.type === 'corkscrew') {
      style = Math.min(1.5, Math.max(0.7, t.peakRate / 800));
    } else if (t.type === 'powerloop') {
      style = 1 + 0.5 * t.meanThrottle;
    }
    const clean = 0.6 + 0.4 * t.purity;
    t.basePoints = Math.round((BASE_POINTS[t.type] * countMult * style * clean) / 5) * 5;
    combo = t.start - lastEnd <= s.comboWindow ? combo + 1 : 0;
    t.comboLevel = combo;
    t.comboMult = Math.min(3, 1 + 0.25 * combo);
    t.points = Math.round(t.basePoints * t.comboMult);
    lastEnd = t.end;
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
