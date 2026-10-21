// Scrollable / zoomable telemetry timeline: gyro rates, throttle, detected tricks, sync markers.
import { TRICK_COLOR, type Analysis } from '../analysis/tricks.ts';
import type { FlightData } from '../types.ts';

export interface TimelineCallbacks {
  onSeek: (t: number) => void;
  /** when set (anchor picking mode) a click picks a log time instead of seeking */
  onPick?: ((t: number) => void) | null;
}

const AXIS_COLORS = ['#ff4d6d', '#3ddc97', '#4cc9f0'];

export class Timeline {
  private ctx: CanvasRenderingContext2D;
  private cache = document.createElement('canvas');
  private cacheKey = '';
  private flight: FlightData | null = null;
  private analysis: Analysis | null = null;
  private v0 = 0;
  private v1 = 1;
  private cursor = 0;
  private hover: number | null = null;
  private videoSpan: [number, number] | null = null;
  private marker: number | null = null;
  private dataVersion = 0;
  private dpr = 1;
  picking = false;

  constructor(private canvas: HTMLCanvasElement, private cb: TimelineCallbacks) {
    this.ctx = canvas.getContext('2d')!;
    this.bind();
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  setData(f: FlightData | null, a: Analysis | null, resetView = true) {
    this.flight = f;
    this.analysis = a;
    this.dataVersion++;
    if (f && resetView) {
      this.v0 = 0;
      this.v1 = Math.max(1, f.info.duration);
    }
    this.draw();
  }

  setCursor(t: number) {
    this.cursor = t;
    // follow playback when cursor leaves the view
    if (this.flight && (t > this.v1 || t < this.v0)) {
      const span = this.v1 - this.v0;
      this.v0 = Math.max(0, t - span * 0.1);
      this.v1 = this.v0 + span;
    }
    this.draw();
  }

  setVideoSpan(span: [number, number] | null) {
    this.videoSpan = span;
    this.draw();
  }

  setMarker(t: number | null) {
    this.marker = t;
    this.draw();
  }

  private x2t(x: number) {
    return this.v0 + (x / this.canvas.clientWidth) * (this.v1 - this.v0);
  }

  private t2x(t: number, w: number) {
    return ((t - this.v0) / (this.v1 - this.v0)) * w;
  }

  private clampView() {
    if (!this.flight) return;
    const dur = Math.max(1, this.flight.info.duration);
    let span = Math.min(dur, Math.max(0.5, this.v1 - this.v0));
    if (this.v0 < 0) this.v0 = 0;
    if (this.v0 + span > dur) this.v0 = Math.max(0, dur - span);
    span = Math.min(span, dur);
    this.v1 = this.v0 + span;
  }

  private bind() {
    let downX = 0;
    let downV0 = 0;
    let dragging = false;
    let pointerDown = false;
    this.canvas.addEventListener('pointerdown', (e) => {
      pointerDown = true;
      dragging = false;
      downX = e.offsetX;
      downV0 = this.v0;
      this.canvas.setPointerCapture(e.pointerId);
    });
    this.canvas.addEventListener('pointermove', (e) => {
      this.hover = this.x2t(e.offsetX);
      if (pointerDown && Math.abs(e.offsetX - downX) > 4) dragging = true;
      if (pointerDown && dragging) {
        const span = this.v1 - this.v0;
        this.v0 = downV0 - ((e.offsetX - downX) / this.canvas.clientWidth) * span;
        this.v1 = this.v0 + span;
        this.clampView();
      }
      this.draw();
    });
    this.canvas.addEventListener('pointerup', (e) => {
      if (pointerDown && !dragging && this.flight) {
        const t = Math.max(0, Math.min(this.flight.info.duration, this.x2t(e.offsetX)));
        if (this.picking && this.cb.onPick) this.cb.onPick(t);
        else this.cb.onSeek(t);
      }
      pointerDown = false;
      dragging = false;
    });
    this.canvas.addEventListener('pointerleave', () => {
      this.hover = null;
      this.draw();
    });
    this.canvas.addEventListener(
      'wheel',
      (e) => {
        if (!this.flight) return;
        e.preventDefault();
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
          const span = this.v1 - this.v0;
          this.v0 += (e.deltaX / this.canvas.clientWidth) * span;
          this.v1 = this.v0 + span;
        } else {
          const t = this.x2t(e.offsetX);
          const k = Math.exp(e.deltaY * 0.0015);
          this.v0 = t - (t - this.v0) * k;
          this.v1 = t + (this.v1 - t) * k;
        }
        this.clampView();
        this.draw();
      },
      { passive: false },
    );
    this.canvas.addEventListener('dblclick', () => {
      if (!this.flight) return;
      this.v0 = 0;
      this.v1 = this.flight.info.duration;
      this.draw();
    });
  }

  private renderCache(w: number, h: number) {
    const key = `${w}x${h}@${this.v0.toFixed(4)}-${this.v1.toFixed(4)}#${this.dataVersion}`;
    if (key === this.cacheKey) return;
    this.cacheKey = key;
    const c = this.cache;
    c.width = w * this.dpr;
    c.height = h * this.dpr;
    const g = c.getContext('2d')!;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const f = this.flight;
    if (!f) return;
    const css = getComputedStyle(this.canvas);
    const grid = css.getPropertyValue('--grid').trim() || 'rgba(255,255,255,0.08)';
    const text = css.getPropertyValue('--muted').trim() || '#8b93a7';
    const top = 22;
    const gyroH = (h - top) * 0.68;
    const thrTop = top + gyroH + 6;
    const thrH = h - thrTop - 4;

    // trick bands
    if (this.analysis) {
      g.font = '600 11px system-ui, sans-serif';
      let labelEnd = -Infinity;
      for (const tr of this.analysis.tricks) {
        const x0 = this.t2x(tr.start, w), x1 = this.t2x(tr.end, w);
        if (x1 < 0 || x0 > w) continue;
        g.fillStyle = TRICK_COLOR[tr.type] + '2a';
        g.fillRect(x0, top, Math.max(2, x1 - x0), h - top);
        g.fillStyle = TRICK_COLOR[tr.type];
        g.fillRect(x0, top, Math.max(2, x1 - x0), 3);
        const lw = g.measureText(tr.name).width;
        if (x0 + 2 > labelEnd + 4) {
          g.fillText(tr.name, x0 + 2, 14);
          labelEnd = x0 + 2 + lw;
        }
      }
      for (const tr of this.analysis.rejected) {
        const x0 = this.t2x(tr.start, w), x1 = this.t2x(tr.end, w);
        if (x1 < 0 || x0 > w) continue;
        g.fillStyle = 'rgba(140,140,160,0.12)';
        g.fillRect(x0, top, Math.max(1, x1 - x0), gyroH);
      }
    }

    // grid + time labels
    const span = this.v1 - this.v0;
    const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const step = steps.find((s) => (s / span) * w > 70) ?? 600;
    g.strokeStyle = grid;
    g.fillStyle = text;
    g.lineWidth = 1;
    g.font = '10px system-ui, sans-serif';
    for (let t = Math.ceil(this.v0 / step) * step; t <= this.v1; t += step) {
      const x = Math.round(this.t2x(t, w)) + 0.5;
      g.beginPath();
      g.moveTo(x, top);
      g.lineTo(x, h);
      g.stroke();
      const label = step < 1 ? t.toFixed(1) : `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`;
      g.fillText(label, x + 3, h - 4);
    }
    const zeroY = top + gyroH / 2;
    g.beginPath();
    g.moveTo(0, zeroY + 0.5);
    g.lineTo(w, zeroY + 0.5);
    g.stroke();

    // min/max per pixel column
    const i0 = Math.max(0, Math.floor((this.v0 - f.t[0]) * f.rate) - 2);
    const i1 = Math.min(f.t.length - 1, Math.ceil((this.v1 - f.t[0]) * f.rate) + 2);
    const maxRate = 1500;
    const drawSeries = (arr: Float32Array, y0: number, hh: number, toY: (v: number) => number, color: string, fill = false) => {
      g.strokeStyle = color;
      g.fillStyle = color + '40';
      g.lineWidth = 1.2;
      g.beginPath();
      let px = -1, mn = 0, mx = 0;
      const pts: [number, number, number][] = [];
      for (let i = i0; i <= i1; i++) {
        const x = Math.floor(this.t2x(f.t[i], w));
        const v = arr[i];
        if (x !== px) {
          if (px >= 0) pts.push([px, mn, mx]);
          px = x;
          mn = mx = v;
        } else {
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      }
      if (px >= 0) pts.push([px, mn, mx]);
      for (const [x, a, b] of pts) {
        g.moveTo(x + 0.5, y0 + toY(a) * hh);
        g.lineTo(x + 0.5, y0 + toY(b) * hh + 0.5);
      }
      g.stroke();
      if (fill) {
        g.beginPath();
        g.moveTo(pts[0]?.[0] ?? 0, y0 + hh);
        for (const [x, , b] of pts) g.lineTo(x, y0 + toY(b) * hh);
        g.lineTo(pts[pts.length - 1]?.[0] ?? 0, y0 + hh);
        g.fill();
      }
    };
    for (let a = 0; a < 3; a++) {
      drawSeries(f.gyro[a], top, gyroH, (v) => 0.5 - Math.max(-1, Math.min(1, v / maxRate)) / 2, AXIS_COLORS[a]);
    }
    drawSeries(f.throttle, thrTop, thrH, (v) => 1 - v, '#ffb703', true);
    g.fillStyle = text;
    g.font = '10px system-ui, sans-serif';
    g.fillText(`±${maxRate}°/s`, 4, top + 11);
    g.fillText('Gas', 4, thrTop + 10);

    // sync beeps
    for (const ev of f.events) {
      if (ev.type !== 'sync_beep') continue;
      const x = this.t2x(ev.time, w);
      g.fillStyle = '#fee440';
      g.beginPath();
      g.moveTo(x - 5, top);
      g.lineTo(x + 5, top);
      g.lineTo(x, top + 8);
      g.fill();
    }
  }

  draw() {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.dpr = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(w * this.dpr) || this.canvas.height !== Math.round(h * this.dpr)) {
      this.canvas.width = Math.round(w * this.dpr);
      this.canvas.height = Math.round(h * this.dpr);
      this.cacheKey = '';
    }
    const g = this.ctx;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.flight) return;
    this.renderCache(w, h);
    g.drawImage(this.cache, 0, 0);
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    if (this.videoSpan) {
      const x0 = this.t2x(this.videoSpan[0], w), x1 = this.t2x(this.videoSpan[1], w);
      g.fillStyle = 'rgba(88,240,168,0.5)';
      g.fillRect(Math.max(0, x0), 18, Math.min(w, x1) - Math.max(0, x0), 3);
    }
    if (this.marker !== null) {
      const x = this.t2x(this.marker, w);
      g.strokeStyle = '#f15bb5';
      g.setLineDash([4, 3]);
      g.beginPath();
      g.moveTo(x, 0);
      g.lineTo(x, h);
      g.stroke();
      g.setLineDash([]);
    }
    if (this.hover !== null) {
      const x = this.t2x(this.hover, w);
      g.strokeStyle = this.picking ? '#f15bb5' : 'rgba(255,255,255,0.35)';
      g.beginPath();
      g.moveTo(x + 0.5, 0);
      g.lineTo(x + 0.5, h);
      g.stroke();
      g.fillStyle = 'rgba(255,255,255,0.85)';
      g.font = '10px system-ui, sans-serif';
      g.fillText(`${this.hover.toFixed(2)} s`, x + 4, 10);
    }
    const cx = this.t2x(this.cursor, w);
    g.strokeStyle = '#58f0a8';
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(cx, 0);
    g.lineTo(cx, h);
    g.stroke();
    g.lineWidth = 1;
  }
}
