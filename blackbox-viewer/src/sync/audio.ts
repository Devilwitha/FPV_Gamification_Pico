// Phase 3: video ↔ telemetry synchronisation.
// Motor noise loudness in the video's audio track follows motor output in the log.
// We cross-correlate both (high-passed, z-normalised) at 50 Hz and pick the best lag.
import type { FlightData } from '../types.ts';

export const SYNC_FS = 50;

export interface SyncResult {
  /** videoTime = logTime + offset */
  offset: number;
  /** peak height in standard deviations of the correlation curve */
  confidence: number;
  curve: Float32Array;
  /** offset (s) of curve[0] and step between samples */
  curveStart: number;
  curveStep: number;
}

export async function audioEnvelope(file: Blob, onProgress?: (msg: string) => void): Promise<Float32Array> {
  onProgress?.('Lese Videodatei…');
  const buf = await file.arrayBuffer();
  onProgress?.('Dekodiere Audiospur…');
  const ctx = new OfflineAudioContext(1, 1, 44100);
  let audio: AudioBuffer;
  try {
    audio = await ctx.decodeAudioData(buf);
  } catch {
    throw new Error('Audiospur konnte nicht dekodiert werden (kein Ton oder Codec nicht unterstützt).');
  }
  onProgress?.('Berechne Hüllkurve…');
  const sr = audio.sampleRate;
  const len = audio.length;
  const chans = Array.from({ length: audio.numberOfChannels }, (_, c) => audio.getChannelData(c));
  const win = Math.round(sr / SYNC_FS);
  const n = Math.floor(len / win);
  const env = new Float32Array(n);
  let prev = 0;
  for (let w = 0; w < n; w++) {
    let s = 0;
    for (let k = w * win; k < (w + 1) * win; k++) {
      let x = 0;
      for (const ch of chans) x += ch[k];
      x /= chans.length;
      const d = x - prev; // first difference = simple high-pass, emphasises motor whine over wind rumble
      prev = x;
      s += d * d;
    }
    env[w] = Math.log10(Math.sqrt(s / win) + 1e-5);
  }
  return env;
}

/** Motor (or throttle) signal resampled to SYNC_FS. */
export function logSignal(f: FlightData): Float32Array {
  const src = f.motor ?? f.throttle;
  const n = Math.floor(f.info.duration * SYNC_FS);
  const out = new Float32Array(n);
  let j = 0;
  for (let i = 0; i < n; i++) {
    const t0 = i / SYNC_FS;
    const t1 = (i + 1) / SYNC_FS;
    let s = 0;
    let c = 0;
    while (j < f.t.length && f.t[j] < t1) {
      if (f.t[j] >= t0) {
        s += src[j];
        c++;
      }
      j++;
    }
    out[i] = c ? s / c : i ? out[i - 1] : 0;
  }
  return out;
}

function highpassNormalize(x: Float32Array, windowSec = 2): Float64Array {
  const n = x.length;
  const w = Math.max(1, Math.round(windowSec * SYNC_FS));
  const prefix = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) prefix[i + 1] = prefix[i] + x[i];
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.max(0, i - w);
    const b = Math.min(n, i + w + 1);
    out[i] = x[i] - (prefix[b] - prefix[a]) / (b - a);
  }
  let m = 0;
  for (const v of out) m += v;
  m /= n || 1;
  let sd = 0;
  for (const v of out) sd += (v - m) ** 2;
  sd = Math.sqrt(sd / (n || 1)) || 1;
  for (let i = 0; i < n; i++) out[i] = (out[i] - m) / sd;
  return out;
}

function fft(re: Float64Array, im: Float64Array, inverse: boolean) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = ((inverse ? 2 : -2) * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2;
        const xr = re[b] * cr - im[b] * ci;
        const xi = re[b] * ci + im[b] * cr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr; im[a] += xi;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
  if (inverse) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
}

/** Finds offset (s) so that video(t) ≈ log(t - offset). */
export function correlate(video: Float32Array, log: Float32Array, minOverlapSec = 15): SyncResult {
  const a = highpassNormalize(video);
  const b = highpassNormalize(log);
  const na = a.length, nb = b.length;
  let N = 1;
  while (N < na + nb) N <<= 1;
  const ar = new Float64Array(N), ai = new Float64Array(N);
  const br = new Float64Array(N), bi = new Float64Array(N);
  ar.set(a);
  br.set(b);
  fft(ar, ai, false);
  fft(br, bi, false);
  // A · conj(B)
  for (let i = 0; i < N; i++) {
    const r = ar[i] * br[i] + ai[i] * bi[i];
    const im = ai[i] * br[i] - ar[i] * bi[i];
    ar[i] = r;
    ai[i] = im;
  }
  fft(ar, ai, true);
  // lag k: c[k] = Σ a[m+k] b[m], k ∈ [-(nb-1), na-1]
  const minOverlap = Math.min(Math.round(minOverlapSec * SYNC_FS), Math.floor(Math.min(na, nb) * 0.5));
  const kMin = -(nb - 1), kMax = na - 1;
  const curve = new Float32Array(kMax - kMin + 1);
  let bestK = 0, best = -Infinity;
  for (let k = kMin; k <= kMax; k++) {
    const overlap = Math.min(na, nb + k) - Math.max(0, k);
    let v = 0;
    if (overlap >= minOverlap) v = ar[(k + N) % N] / overlap;
    curve[k - kMin] = v;
    if (overlap >= minOverlap && v > best) {
      best = v;
      bestK = k;
    }
  }
  let m = 0, c = 0;
  for (const v of curve) if (v) { m += v; c++; }
  m /= c || 1;
  let sd = 0;
  for (const v of curve) if (v) sd += (v - m) ** 2;
  sd = Math.sqrt(sd / (c || 1)) || 1;
  return { offset: bestK / SYNC_FS, confidence: (best - m) / sd, curve, curveStart: kMin / SYNC_FS, curveStep: 1 / SYNC_FS };
}

/** Times (s) where the throttle is punched up quickly – handy sync anchors. */
export function throttlePunches(f: FlightData): number[] {
  const out: number[] = [];
  const look = Math.round(0.25 * f.rate);
  let i = 0;
  while (i + look < f.t.length) {
    if (f.throttle[i + look] - f.throttle[i] > 0.3) {
      // onset: first sample rising noticeably
      let j = i;
      while (j < i + look && f.throttle[j + 1] - f.throttle[j] < 0.002) j++;
      out.push(f.t[j]);
      i += Math.round(1.0 * f.rate);
    } else i += 2;
  }
  return out;
}
