/** First-order low-pass, run forward and backward for zero phase lag. */
export function pt1ZeroPhase(x: ArrayLike<number>, rate: number, cutoffHz: number): Float32Array {
  const n = x.length;
  const out = new Float32Array(n);
  if (!n) return out;
  const rc = 1 / (2 * Math.PI * cutoffHz);
  const k = 1 / rate / (rc + 1 / rate);
  let y = x[0];
  for (let i = 0; i < n; i++) out[i] = y += k * (x[i] - y);
  y = out[n - 1];
  for (let i = n - 1; i >= 0; i--) out[i] = y += k * (out[i] - y);
  return out;
}

/** Trapezoidal running integral of x over time t (same units as x * seconds). */
export function cumulative(x: ArrayLike<number>, t: ArrayLike<number>): Float64Array {
  const n = x.length;
  const out = new Float64Array(n);
  for (let i = 1; i < n; i++) {
    const dt = Math.min(0.05, Math.max(0, t[i] - t[i - 1]));
    out[i] = out[i - 1] + 0.5 * (x[i] + x[i - 1]) * dt;
  }
  return out;
}

/** Index of the last sample with t[i] <= time (binary search). */
export function indexAt(t: ArrayLike<number>, time: number): number {
  let lo = 0;
  let hi = t.length - 1;
  if (hi < 0) return 0;
  if (time <= t[0]) return 0;
  if (time >= t[hi]) return hi;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t[mid] <= time) lo = mid;
    else hi = mid;
  }
  return lo;
}
