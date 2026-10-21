// Extension: learnable trick classification.
// Every correction the pilot makes in the trick list is stored as a labelled feature vector.
// A distance-weighted k-nearest-neighbour model on standardised features then re-labels
// candidates (including ones the rule engine rejected).
import type { TrickType } from '../types.ts';

export interface Example {
  f: number[];
  label: TrickType;
}

const KEY = 'bba.ml.examples.v1';
export const FEATURE_NAMES = ['Roll (U)', 'Pitch (U)', 'Yaw (U)', 'Dauer s', 'Gas Ø', 'Peak k°/s', 'Reihenfolge'];
export const MIN_EXAMPLES = 6;

export class TrickClassifier {
  examples: Example[] = [];
  enabled = false;
  k = 5;

  constructor() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const data = JSON.parse(raw);
        this.examples = Array.isArray(data.examples) ? data.examples : [];
        this.enabled = !!data.enabled;
      }
    } catch {
      /* storage unavailable */
    }
  }

  save() {
    try {
      localStorage.setItem(KEY, JSON.stringify({ examples: this.examples, enabled: this.enabled }));
    } catch {
      /* ignore */
    }
  }

  add(f: number[], label: TrickType) {
    // replace a near-identical example (same candidate relabelled twice)
    this.examples = this.examples.filter((e) => e.f.some((v, i) => Math.abs(v - f[i]) > 1e-6));
    this.examples.push({ f: f.slice(), label });
    this.save();
  }

  clear() {
    this.examples = [];
    this.save();
  }

  get ready() {
    return this.enabled && this.examples.length >= MIN_EXAMPLES;
  }

  counts(): Partial<Record<TrickType, number>> {
    const c: Partial<Record<TrickType, number>> = {};
    for (const e of this.examples) c[e.label] = (c[e.label] ?? 0) + 1;
    return c;
  }

  private scales(): number[] {
    const dims = this.examples[0]?.f.length ?? 0;
    const out: number[] = [];
    for (let d = 0; d < dims; d++) {
      let m = 0;
      for (const e of this.examples) m += e.f[d];
      m /= this.examples.length;
      let v = 0;
      for (const e of this.examples) v += (e.f[d] - m) ** 2;
      out.push(Math.max(0.05, Math.sqrt(v / this.examples.length)));
    }
    return out;
  }

  predict(f: number[]): { label: TrickType; confidence: number } | null {
    if (!this.ready) return null;
    const sc = this.scales();
    const scored = this.examples
      .map((e) => {
        let d = 0;
        for (let i = 0; i < sc.length; i++) d += ((e.f[i] - f[i]) / sc[i]) ** 2;
        return { e, d: Math.sqrt(d) };
      })
      .sort((a, b) => a.d - b.d)
      .slice(0, this.k);
    const votes = new Map<TrickType, number>();
    let total = 0;
    for (const { e, d } of scored) {
      const w = 1 / (d + 0.1);
      votes.set(e.label, (votes.get(e.label) ?? 0) + w);
      total += w;
    }
    let best: TrickType = 'none';
    let bestW = -1;
    for (const [label, w] of votes) if (w > bestW) [best, bestW] = [label, w];
    return { label: best, confidence: bestW / total };
  }

  exportJson(): string {
    return JSON.stringify({ version: 1, features: FEATURE_NAMES, examples: this.examples }, null, 1);
  }

  importJson(text: string): number {
    const data = JSON.parse(text);
    const list: Example[] = (data.examples ?? []).filter(
      (e: Example) => Array.isArray(e.f) && e.f.length === FEATURE_NAMES.length && typeof e.label === 'string',
    );
    this.examples.push(...list);
    this.save();
    return list.length;
  }
}
