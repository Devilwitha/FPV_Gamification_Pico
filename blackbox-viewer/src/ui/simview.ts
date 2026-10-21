// Synthetic FPV camera view rendered from the attitude estimate – shown when no video is loaded.
import { pt1ZeroPhase } from '../analysis/filters.ts';
import { rotationAt } from '../analysis/orientation.ts';
import type { FlightData } from '../types.ts';

const W = 320;
const H = 180;
const CAM_TILT = (25 * Math.PI) / 180;
const HFOV = (118 * Math.PI) / 180;

export interface Track {
  x: Float32Array;
  y: Float32Array;
  z: Float32Array;
}

/** Fake position track: fly along the nose direction, speed from throttle. Only for the sim view. */
export function computeTrack(f: FlightData): Track {
  const n = f.t.length;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  const thr = pt1ZeroPhase(f.throttle, f.rate, 0.4);
  const m = new Float64Array(9);
  let px = 0, py = 0;
  for (let i = 1; i < n; i++) {
    rotationAt(f.quat, i, m);
    const dt = Math.min(0.05, f.t[i] - f.t[i - 1]);
    const speed = thr[i] > 0.08 ? 4 + 22 * thr[i] : 0;
    const hx = m[0], hy = m[3];
    const hn = Math.hypot(hx, hy) || 1;
    px += (hx / hn) * speed * dt;
    py += (hy / hn) * speed * dt;
    x[i] = px;
    y[i] = py;
    z[i] = 0.4 + Math.max(0, thr[i] - 0.05) * 18;
  }
  return { x, y, z };
}

export class SimView {
  private canvas = document.createElement('canvas');
  private ctx: CanvasRenderingContext2D;
  private img: ImageData;
  private dirs = new Float32Array(W * H * 3);
  private m = new Float64Array(9);

  constructor() {
    this.canvas.width = W;
    this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d')!;
    this.img = this.ctx.createImageData(W, H);
    const f = W / 2 / Math.tan(HFOV / 2);
    const c = Math.cos(CAM_TILT), s = Math.sin(CAM_TILT);
    for (let py = 0; py < H; py++) {
      for (let px = 0; px < W; px++) {
        // camera ray: forward 1, left = -screen x, up = -screen y
        const cx = 1, cy = -(px - W / 2) / f, cz = -(py - H / 2) / f;
        const nrm = 1 / Math.hypot(cx, cy, cz);
        const k = (py * W + px) * 3;
        // tilt camera up around body y
        this.dirs[k] = (cx * c - cz * s) * nrm;
        this.dirs[k + 1] = cy * nrm;
        this.dirs[k + 2] = (cx * s + cz * c) * nrm;
      }
    }
  }

  draw(out: CanvasRenderingContext2D, w: number, h: number, flight: FlightData, i: number, track: Track | null) {
    const m = rotationAt(flight.quat, i, this.m);
    const d = this.img.data;
    const ox = track ? track.x[i] : 0;
    const oy = track ? track.y[i] : 0;
    const alt = track ? track.z[i] + 1.2 : 3;
    for (let p = 0, k = 0, q = 0; p < W * H; p++, k += 3, q += 4) {
      const bx = this.dirs[k], by = this.dirs[k + 1], bz = this.dirs[k + 2];
      const wx = m[0] * bx + m[1] * by + m[2] * bz;
      const wy = m[3] * bx + m[4] * by + m[5] * bz;
      const wz = m[6] * bx + m[7] * by + m[8] * bz;
      let r: number, g: number, b: number;
      if (wz < -0.002) {
        const dist = alt / -wz;
        const gx = ox + wx * dist;
        const gy = oy + wy * dist;
        const checker = ((Math.floor(gx / 6) + Math.floor(gy / 6)) & 1) === 0;
        const lane = Math.abs(((gy % 60) + 60) % 60 - 30) < 1.2 || Math.abs(((gx % 60) + 60) % 60 - 30) < 1.2;
        r = lane ? 196 : checker ? 58 : 70;
        g = lane ? 186 : checker ? 122 : 138;
        b = lane ? 160 : checker ? 52 : 60;
        const fog = Math.min(1, dist / 260);
        r += (178 - r) * fog;
        g += (196 - g) * fog;
        b += (214 - b) * fog;
      } else {
        const e = Math.min(1, wz * 1.6);
        r = 178 + (40 - 178) * e;
        g = 200 + (110 - 200) * e;
        b = 222 + (200 - 222) * e;
        const sun = wx * 0.6 + wy * 0.5 + wz * 0.62;
        if (sun > 0.995) r = g = b = 255;
        else if (sun > 0.97) {
          const glow = (sun - 0.97) / 0.025;
          r += (255 - r) * glow * 0.6;
          g += (250 - g) * glow * 0.6;
          b += (230 - b) * glow * 0.4;
        }
      }
      d[q] = r;
      d[q + 1] = g;
      d[q + 2] = b;
      d[q + 3] = 255;
    }
    this.ctx.putImageData(this.img, 0, 0);
    out.imageSmoothingEnabled = true;
    out.drawImage(this.canvas, 0, 0, w, h);
    // analog FPV feel: vignette + scanlines
    const vg = out.createRadialGradient(w / 2, h / 2, h * 0.35, w / 2, h / 2, h * 0.95);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.55)');
    out.fillStyle = vg;
    out.fillRect(0, 0, w, h);
    out.fillStyle = 'rgba(0,0,0,0.08)';
    for (let yy = 0; yy < h; yy += 3) out.fillRect(0, yy, w, 1);
  }
}
