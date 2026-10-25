// Community model: finds tricks the rule engine misses, learned from marked examples.
//
// Pilots mark a span in the timeline ("trick not detected") and pick a trick or request a new one.
// The admin reviews a short video clip and approves it; approved marks become examples (feature
// vector + label) served by the website (/api/tricks/katalog). Here every flight is scanned with
// sliding windows of the example durations; a window whose features are close to an example (scaled
// Euclidean distance below MATCH_DIST) becomes a trick, best matches first, without overlapping
// tricks the rules already found.
import type { FlightData, Vec3Arr } from '../types.ts';

export interface LabelExample {
  label: string;
  f: number[];
}

export interface LearnedHit {
  i0: number;
  i1: number;
  label: string;
  dist: number;
}

/** Feature scales (same order as spanFeatures): turns roll/pitch/yaw, duration (relative), throttle, peak k°/s, order. */
const SCALES = [0.12, 0.12, 0.12, 0, 0.12, 0.15, 0.4];
export const MATCH_DIST = 1.5;
const STEP_SECONDS = 0.05;
const DURATION_FACTORS = [0.9, 1, 1.1];
const MAX_EXAMPLES_PER_LABEL = 25;

export function exampleDistance(window: number[], example: number[]): number {
  let d = 0;
  for (let i = 0; i < SCALES.length; i++) {
    const s = i === 3 ? 0.1 + 0.2 * example[3] : SCALES[i];
    d += ((window[i] - example[i]) / s) ** 2;
  }
  return Math.sqrt(d);
}

/**
 * Examples usable for the window search. Spans without a real rotation (hangs, dives, cruising)
 * cannot be told apart from normal flying by these features, so they only train the local
 * classifier, not the search.
 */
export function searchable(e: LabelExample): boolean {
  if (e.label === 'none' || e.f.length !== SCALES.length || e.f.some((v) => !Number.isFinite(v))) return false;
  const turns = Math.max(e.f[0], e.f[1], e.f[2]);
  return (turns >= 0.25 || e.f[5] >= 0.3) && e.f[3] >= 0.15 && e.f[3] <= 30;
}

/** O(1) span features via prefix sums and a block-maximum sparse table (≈ spanFeatures()). */
class FeatureIndex {
  private thr: Float64Array;
  private wR: Float64Array;
  private cR: Float64Array;
  private wP: Float64Array;
  private cP: Float64Array;
  private block: number;
  private table: Float32Array[] = [];
  private f: FlightData;
  private cum: [Float64Array, Float64Array, Float64Array];

  constructor(f: FlightData, cum: [Float64Array, Float64Array, Float64Array], smooth: Vec3Arr) {
    this.f = f;
    this.cum = cum;
    const n = f.t.length;
    this.thr = new Float64Array(n + 1);
    this.wR = new Float64Array(n + 1);
    this.cR = new Float64Array(n + 1);
    this.wP = new Float64Array(n + 1);
    this.cP = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) {
      const r = Math.abs(smooth[0][i]), p = Math.abs(smooth[1][i]);
      this.thr[i + 1] = this.thr[i] + f.throttle[i];
      this.wR[i + 1] = this.wR[i] + r;
      this.cR[i + 1] = this.cR[i] + r * f.t[i];
      this.wP[i + 1] = this.wP[i] + p;
      this.cP[i + 1] = this.cP[i] + p * f.t[i];
    }
    // peak rate per block of ~20 ms, then a sparse table for range maxima
    this.block = Math.max(1, Math.round(f.rate / 50));
    const blocks = Math.ceil(n / this.block);
    const base = new Float32Array(blocks);
    for (let i = 0; i < n; i++) {
      const v = Math.max(Math.abs(smooth[0][i]), Math.abs(smooth[1][i]), Math.abs(smooth[2][i]));
      const b = (i / this.block) | 0;
      if (v > base[b]) base[b] = v;
    }
    this.table.push(base);
    for (let k = 1; 1 << k <= blocks; k++) {
      const prev = this.table[k - 1];
      const len = blocks - (1 << k) + 1;
      const next = new Float32Array(len);
      for (let i = 0; i < len; i++) next[i] = Math.max(prev[i], prev[i + (1 << (k - 1))]);
      this.table.push(next);
    }
  }

  private peak(i0: number, i1: number): number {
    const b0 = (i0 / this.block) | 0, b1 = (i1 / this.block) | 0;
    const k = 31 - Math.clz32(b1 - b0 + 1);
    const row = this.table[k];
    return Math.max(row[b0], row[b1 - (1 << k) + 1]);
  }

  features(i0: number, i1: number): number[] {
    const { t } = this.f;
    const duration = t[i1] - t[i0];
    const count = i1 - i0 + 1;
    const wR = this.wR[i1 + 1] - this.wR[i0], wP = this.wP[i1 + 1] - this.wP[i0];
    const cR = this.cR[i1 + 1] - this.cR[i0], cP = this.cP[i1 + 1] - this.cP[i0];
    const order = wR > 0 && wP > 0 && duration > 0 ? Math.max(-1, Math.min(1, (cP / wP - cR / wR) / duration)) : 0;
    return [
      Math.abs(this.cum[0][i1] - this.cum[0][i0]) / 360,
      Math.abs(this.cum[1][i1] - this.cum[1][i0]) / 360,
      Math.abs(this.cum[2][i1] - this.cum[2][i0]) / 360,
      duration,
      (this.thr[i1 + 1] - this.thr[i0]) / count,
      this.peak(i0, i1) / 1000,
      order,
    ];
  }
}

/** Share of [i0, i1] covered by the given spans (0..1). */
function covered(t: Float64Array, i0: number, i1: number, spans: [number, number][]): number {
  const len = t[i1] - t[i0] || 1e-6;
  let sum = 0;
  for (const [a, b] of spans) sum += Math.max(0, Math.min(t[i1], t[b]) - Math.max(t[i0], t[a]));
  return Math.min(1, sum / len);
}

/**
 * Sliding-window search for the given examples. `occupied` are spans of tricks already found;
 * hits may overlap them by at most 30 %. Returns non-overlapping hits sorted by time.
 */
export function findLearnedTricks(
  f: FlightData,
  cum: [Float64Array, Float64Array, Float64Array],
  smooth: Vec3Arr,
  examples: LabelExample[],
  occupied: [number, number][],
  maxDist = MATCH_DIST,
): LearnedHit[] {
  const n = f.t.length;
  if (n < 10 || !examples.length) return [];
  // newest examples first (the server sends them that way), capped per label
  const perLabel = new Map<string, number>();
  const usable = examples.filter((e) => {
    if (!searchable(e)) return false;
    const c = perLabel.get(e.label) ?? 0;
    perLabel.set(e.label, c + 1);
    return c < MAX_EXAMPLES_PER_LABEL;
  });
  if (!usable.length) return [];

  // window length in samples → examples to compare
  const byLength = new Map<number, LabelExample[]>();
  for (const e of usable) {
    for (const k of DURATION_FACTORS) {
      const len = Math.max(3, Math.round((Math.round((e.f[3] * k) / STEP_SECONDS) * STEP_SECONDS) * f.rate));
      if (len >= n) continue;
      const list = byLength.get(len) ?? [];
      list.push(e);
      byLength.set(len, list);
    }
  }

  const index = new FeatureIndex(f, cum, smooth);
  const step = Math.max(1, Math.round(f.rate * STEP_SECONDS));
  const hits: LearnedHit[] = [];
  for (const [len, list] of byLength) {
    for (let i0 = 0; i0 + len < n; i0 += step) {
      const i1 = i0 + len;
      const feat = index.features(i0, i1);
      // quick reject: idle on the ground or barely rotating
      if (feat[5] < 0.15) continue;
      let best: LabelExample | null = null;
      let bestDist = maxDist;
      for (const e of list) {
        const d = exampleDistance(feat, e.f);
        if (d < bestDist) [best, bestDist] = [e, d];
      }
      if (best) hits.push({ i0, i1, label: best.label, dist: bestDist });
    }
  }

  hits.sort((a, b) => a.dist - b.dist);
  const accepted: LearnedHit[] = [];
  const taken: [number, number][] = [...occupied];
  for (const h of hits) {
    if (covered(f.t, h.i0, h.i1, taken) > 0.3) continue;
    // and no accepted hit may be mostly inside this one
    if (accepted.some((a) => covered(f.t, a.i0, a.i1, [[h.i0, h.i1]]) > 0.3)) continue;
    accepted.push(h);
    taken.push([h.i0, h.i1]);
  }
  return accepted.sort((a, b) => a.i0 - b.i0);
}
