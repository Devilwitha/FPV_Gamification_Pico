// Phase 4: HUD overlay – live score, trick pop-ups, combo meter, rotation gauge, sticks, attitude.
import { indexAt } from '../analysis/filters.ts';
import { rotationAt } from '../analysis/orientation.ts';
import { HOLD_TRICKS, TRICK_COLOR, type Analysis } from '../analysis/tricks.ts';
import type { FlightData, Trick } from '../types.ts';

export interface HudOptions {
  score: boolean;
  popups: boolean;
  gauge: boolean;
  sticks: boolean;
  telemetry: boolean;
  attitude: boolean;
}

export const DEFAULT_HUD: HudOptions = { score: true, popups: true, gauge: true, sticks: true, telemetry: true, attitude: true };

const FONT = '"Segoe UI", "Inter", system-ui, -apple-system, sans-serif';
const POPUP_TIME = 2.4;

const easeOutBack = (x: number) => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2);

export class Hud {
  private shownScore = 0;
  private lastT = 0;
  private m = new Float64Array(9);

  draw(
    ctx: CanvasRenderingContext2D, w: number, h: number,
    f: FlightData, i: number, t: number,
    analysis: Analysis, comboWindow: number, opt: HudOptions,
  ) {
    const s = h / 720;
    const tricks = analysis.tricks;
    let target = 0;
    let last: Trick | null = null;
    let active: Trick | null = null;
    const done: Trick[] = [];
    for (const tr of tricks) {
      if (tr.end <= t) {
        target += tr.points;
        last = tr;
        done.push(tr);
      } else if (tr.start <= t) active = tr;
    }
    // animate score; jump when seeking backwards or far
    const dt = Math.abs(t - this.lastT);
    this.lastT = t;
    if (dt > 0.5 || target < this.shownScore) this.shownScore = target;
    else this.shownScore += (target - this.shownScore) * Math.min(1, dt * 8 + 0.04);
    if (Math.abs(target - this.shownScore) < 1) this.shownScore = target;

    ctx.save();
    ctx.textBaseline = 'alphabetic';
    this.topLeft(ctx, s, t, f);
    if (opt.score) this.scoreBlock(ctx, w, s, t, last, done, comboWindow);
    if (opt.gauge && active) this.gauge(ctx, w, h, s, f, i, active, analysis);
    if (opt.popups && last && t - last.end < POPUP_TIME) this.popup(ctx, w, h, s, last, t - last.end);
    if (opt.sticks) this.sticksBlock(ctx, h, s, f, i);
    if (opt.telemetry) this.telemetry(ctx, w, h, s, f, i);
    if (opt.attitude) this.attitude(ctx, w, h, s, f, i);
    ctx.restore();
  }

  private panel(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.fillStyle = 'rgba(8, 11, 13, 0.55)';
    ctx.fill();
  }

  private topLeft(ctx: CanvasRenderingContext2D, s: number, t: number, f: FlightData) {
    const x = 24 * s, y = 24 * s;
    this.panel(ctx, x, y, 210 * s, 58 * s, 10 * s);
    ctx.fillStyle = '#58f0a8';
    ctx.font = `700 ${12 * s}px ${FONT}`;
    ctx.fillText('BLACKBOX ARENA', x + 12 * s, y + 20 * s);
    ctx.fillStyle = '#fff';
    ctx.font = `600 ${22 * s}px ${FONT}`;
    const mm = Math.floor(Math.max(0, t) / 60);
    const ss = (Math.max(0, t) % 60).toFixed(2).padStart(5, '0');
    ctx.fillText(`T+ ${mm}:${ss}`, x + 12 * s, y + 46 * s);
    if (f.info.craft) {
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.font = `500 ${12 * s}px ${FONT}`;
      ctx.textAlign = 'right';
      ctx.fillText(f.info.craft.slice(0, 14), x + 198 * s, y + 20 * s);
      ctx.textAlign = 'left';
    }
  }

  private scoreBlock(ctx: CanvasRenderingContext2D, w: number, s: number, t: number, last: Trick | null, done: Trick[], comboWindow: number) {
    const bw = 260 * s;
    const x = w - bw - 24 * s, y = 24 * s;
    this.panel(ctx, x, y, bw, 92 * s, 12 * s);
    ctx.textAlign = 'right';
    ctx.fillStyle = 'rgba(255,255,255,0.65)';
    ctx.font = `700 ${12 * s}px ${FONT}`;
    ctx.fillText('SCORE', x + bw - 16 * s, y + 22 * s);
    ctx.fillStyle = '#fff';
    ctx.font = `800 italic ${46 * s}px ${FONT}`;
    ctx.shadowColor = 'rgba(88,240,168,0.6)';
    ctx.shadowBlur = 16 * s;
    ctx.fillText(Math.round(this.shownScore).toLocaleString('de-DE'), x + bw - 16 * s, y + 70 * s);
    ctx.shadowBlur = 0;
    ctx.textAlign = 'left';
    ctx.fillStyle = 'rgba(255,255,255,0.65)';
    ctx.font = `600 ${12 * s}px ${FONT}`;
    ctx.fillText(`${done.length} TRICKS`, x + 16 * s, y + 22 * s);

    // combo window countdown
    if (last && t - last.end <= comboWindow) {
      const rem = 1 - (t - last.end) / comboWindow;
      const next = Math.min(3, 1 + 0.25 * (last.comboLevel + 1));
      ctx.fillStyle = '#fee440';
      ctx.font = `800 italic ${16 * s}px ${FONT}`;
      ctx.fillText(`COMBO ×${next.toFixed(2)}`, x + 16 * s, y + 46 * s);
      ctx.fillStyle = 'rgba(255,255,255,0.15)';
      ctx.fillRect(x + 16 * s, y + 80 * s, bw - 32 * s, 4 * s);
      ctx.fillStyle = '#fee440';
      ctx.fillRect(x + 16 * s, y + 80 * s, (bw - 32 * s) * rem, 4 * s);
    }

    // trick feed
    const feed = done.slice(-4).reverse();
    let fy = y + 112 * s;
    feed.forEach((tr, k) => {
      const age = t - tr.end;
      const alpha = Math.max(0.25, 1 - k * 0.2) * Math.min(1, age * 4 + 0.3);
      ctx.globalAlpha = alpha;
      this.panel(ctx, x + 40 * s, fy, bw - 40 * s, 30 * s, 8 * s);
      ctx.fillStyle = TRICK_COLOR[tr.type];
      ctx.fillRect(x + 40 * s, fy, 4 * s, 30 * s);
      ctx.fillStyle = '#fff';
      ctx.font = `700 ${14 * s}px ${FONT}`;
      ctx.fillText(tr.name, x + 54 * s, fy + 20 * s);
      ctx.textAlign = 'right';
      ctx.fillStyle = '#58f0a8';
      ctx.fillText(`+${tr.points}`, x + bw - 12 * s, fy + 20 * s);
      ctx.textAlign = 'left';
      fy += 36 * s;
    });
    ctx.globalAlpha = 1;
  }

  private popup(ctx: CanvasRenderingContext2D, w: number, h: number, s: number, tr: Trick, age: number) {
    const inT = Math.min(1, age / 0.28);
    const scale = easeOutBack(inT);
    const alpha = age > POPUP_TIME - 0.5 ? (POPUP_TIME - age) / 0.5 : 1;
    const cx = w / 2, cy = h * 0.3;
    ctx.save();
    ctx.globalAlpha = Math.max(0, alpha);
    ctx.translate(cx, cy);
    ctx.scale(scale, scale);
    ctx.rotate(-0.04);
    ctx.textAlign = 'center';
    const color = TRICK_COLOR[tr.type];
    ctx.font = `900 italic ${64 * s}px ${FONT}`;
    ctx.lineWidth = 8 * s;
    ctx.strokeStyle = 'rgba(0,0,0,0.65)';
    ctx.strokeText(tr.name.toUpperCase(), 0, 0);
    ctx.shadowColor = color;
    ctx.shadowBlur = 24 * s;
    ctx.fillStyle = color;
    ctx.fillText(tr.name.toUpperCase(), 0, 0);
    ctx.shadowBlur = 0;
    ctx.font = `800 italic ${34 * s}px ${FONT}`;
    ctx.strokeStyle = 'rgba(0,0,0,0.6)';
    ctx.lineWidth = 6 * s;
    const pts = `+${tr.points}`;
    ctx.strokeText(pts, 0, 46 * s);
    ctx.fillStyle = '#fff';
    ctx.fillText(pts, 0, 46 * s);
    if (tr.comboLevel > 0) {
      ctx.font = `800 ${18 * s}px ${FONT}`;
      ctx.fillStyle = '#fee440';
      ctx.fillText(`${tr.comboLevel + 1}er COMBO  ×${tr.comboMult.toFixed(2)}`, 0, 76 * s);
    }
    ctx.restore();
  }

  private gauge(ctx: CanvasRenderingContext2D, w: number, h: number, s: number, f: FlightData, i: number, tr: Trick, a: Analysis) {
    const i0 = Math.min(i, indexAt(f.t, tr.start));
    let angle: number;
    let goal: number;
    let label = '';
    if (HOLD_TRICKS.includes(tr.type)) {
      angle = Math.max(0, f.t[i] - tr.start);
      goal = Math.max(0.1, tr.end - tr.start);
      label = `${Math.min(angle, goal).toFixed(1)} s`;
    } else if (tr.type === 'splits' || tr.type === 'immelmann') {
      angle = Math.abs(a.cum[0][i] - a.cum[0][i0]) + Math.abs(a.cum[1][i] - a.cum[1][i0]);
      goal = 360;
    } else {
      angle = Math.abs(a.cum[tr.axis][i] - a.cum[tr.axis][i0]);
      goal = 360 * Math.max(1, tr.count);
    }
    const p = Math.min(1, angle / goal);
    const cx = w / 2, cy = h / 2, r = 70 * s;
    const color = TRICK_COLOR[tr.type];
    ctx.lineWidth = 8 * s;
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 12 * s;
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + p * Math.PI * 2);
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.textAlign = 'center';
    ctx.fillStyle = '#fff';
    ctx.font = `800 italic ${22 * s}px ${FONT}`;
    ctx.fillText(label || `${Math.round(angle)}°`, cx, cy + 8 * s);
    ctx.textAlign = 'left';
  }

  private sticksBlock(ctx: CanvasRenderingContext2D, h: number, s: number, f: FlightData, i: number) {
    const size = 84 * s, gap = 14 * s;
    const x = 24 * s, y = h - size - 24 * s;
    const boxes: [number, number][] = [
      [-f.sticks[2][i], f.throttle[i] * 2 - 1], // mode 2: yaw / throttle
      [f.sticks[0][i], f.sticks[1][i]], // roll / pitch
    ];
    boxes.forEach(([sx, sy], k) => {
      const bx = x + k * (size + gap);
      this.panel(ctx, bx, y, size, size, 10 * s);
      ctx.strokeStyle = 'rgba(255,255,255,0.18)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(bx + size / 2, y + 8 * s);
      ctx.lineTo(bx + size / 2, y + size - 8 * s);
      ctx.moveTo(bx + 8 * s, y + size / 2);
      ctx.lineTo(bx + size - 8 * s, y + size / 2);
      ctx.stroke();
      const px = bx + size / 2 + (sx * (size / 2 - 12 * s));
      const py = y + size / 2 - (sy * (size / 2 - 12 * s));
      ctx.fillStyle = '#58f0a8';
      ctx.shadowColor = '#58f0a8';
      ctx.shadowBlur = 10 * s;
      ctx.beginPath();
      ctx.arc(px, py, 7 * s, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    });
  }

  private telemetry(ctx: CanvasRenderingContext2D, w: number, h: number, s: number, f: FlightData, i: number) {
    const bw = 190 * s, bh = 92 * s;
    const x = w - bw - 24 * s, y = h - bh - 24 * s;
    this.panel(ctx, x, y, bw, bh, 10 * s);
    // throttle bar
    const thr = f.throttle[i];
    const barH = bh - 24 * s;
    ctx.fillStyle = 'rgba(255,255,255,0.15)';
    ctx.fillRect(x + 14 * s, y + 12 * s, 12 * s, barH);
    const grad = ctx.createLinearGradient(0, y + 12 * s + barH, 0, y + 12 * s);
    grad.addColorStop(0, '#3ddc97');
    grad.addColorStop(0.7, '#ffb703');
    grad.addColorStop(1, '#ff4d6d');
    ctx.fillStyle = grad;
    ctx.fillRect(x + 14 * s, y + 12 * s + barH * (1 - thr), 12 * s, barH * thr);
    ctx.fillStyle = '#fff';
    ctx.font = `700 ${13 * s}px ${FONT}`;
    ctx.fillText(`THR ${Math.round(thr * 100)}%`, x + 38 * s, y + 24 * s);
    const labels = ['R', 'P', 'Y'];
    const colors = ['#ff4d6d', '#3ddc97', '#4cc9f0'];
    ctx.font = `600 ${12 * s}px ${FONT}`;
    for (let a = 0; a < 3; a++) {
      ctx.fillStyle = colors[a];
      ctx.fillText(labels[a], x + 38 * s, y + (44 + a * 17) * s);
      ctx.fillStyle = 'rgba(255,255,255,0.9)';
      ctx.textAlign = 'right';
      ctx.fillText(`${Math.round(f.gyro[a][i])}°/s`, x + bw - 14 * s, y + (44 + a * 17) * s);
      ctx.textAlign = 'left';
    }
  }

  private attitude(ctx: CanvasRenderingContext2D, w: number, h: number, s: number, f: FlightData, i: number) {
    const m = rotationAt(f.quat, i, this.m);
    const psi = Math.atan2(m[3], m[0]);
    const c = Math.cos(-psi), sn = Math.sin(-psi);
    const el = 0.45;
    const ce = Math.cos(el), se = Math.sin(el);
    const cx = w / 2, cy = h - 70 * s, scale = 38 * s;
    const proj = (bx: number, by: number, bz: number): [number, number, number] => {
      let wx = m[0] * bx + m[1] * by + m[2] * bz;
      let wy = m[3] * bx + m[4] * by + m[5] * bz;
      const wz = m[6] * bx + m[7] * by + m[8] * bz;
      const rx = c * wx - sn * wy;
      const ry = sn * wx + c * wy;
      wx = rx;
      wy = ry;
      return [cx - wy * scale, cy - (wz * ce + wx * se) * scale, wx * ce - wz * se];
    };
    this.panel(ctx, cx - 70 * s, cy - 52 * s, 140 * s, 100 * s, 12 * s);
    const arms: [number, number][] = [[0.7, 0.7], [0.7, -0.7], [-0.7, 0.7], [-0.7, -0.7]];
    ctx.lineWidth = 3 * s;
    ctx.strokeStyle = 'rgba(255,255,255,0.85)';
    for (const [ax, ay] of arms) {
      const a = proj(0, 0, 0), b = proj(ax, ay, 0);
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
    }
    for (const [ax, ay] of arms) {
      ctx.beginPath();
      for (let k = 0; k <= 16; k++) {
        const ang = (k / 16) * Math.PI * 2;
        const p = proj(ax + Math.cos(ang) * 0.32, ay + Math.sin(ang) * 0.32, 0);
        if (k) ctx.lineTo(p[0], p[1]);
        else ctx.moveTo(p[0], p[1]);
      }
      ctx.strokeStyle = ax > 0 ? '#ff4d6d' : '#58f0a8';
      ctx.lineWidth = 2 * s;
      ctx.stroke();
    }
    // up marker (camera side)
    const top = proj(0, 0, 0.35), base = proj(0, 0, 0);
    ctx.strokeStyle = '#fee440';
    ctx.beginPath();
    ctx.moveTo(base[0], base[1]);
    ctx.lineTo(top[0], top[1]);
    ctx.stroke();
  }
}
