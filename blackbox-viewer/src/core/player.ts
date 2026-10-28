// FlightPlayer: one flight on one canvas – the single source for playback, video sync (offset + drift),
// trick analysis and rendering (video or sim view + HUD). The single viewer (main.ts) uses one instance,
// the VS viewer (vs.ts) two of them side by side. Changes to analysis or rendering happen here (and in
// analysis/, ui/hud.ts) and apply to every view.
//
// Two ways to play:
//  - with a decoded log (setFlight): HUD drawn live, tricks detected with the current settings;
//  - replay of a recorded clip with burned-in HUD (setReplay): only the video, log times come from the
//    clip's start time – used for VS matches, whose logs stay private on the server.
import { indexAt } from '../analysis/filters.ts';
import type { LabelExample } from '../analysis/learned.ts';
import { DEFAULT_SETTINGS, detectTricks, type Analysis, type DetectSettings, type Labeler } from '../analysis/tricks.ts';
import type { FlightData, TrickType } from '../types.ts';
import { DEFAULT_HUD, Hud, type HudOptions } from '../ui/hud.ts';
import { SimView, computeTrack, type Track } from '../ui/simview.ts';

export interface PlayerOptions {
  settings?: DetectSettings;
  hud?: HudOptions;
  /** draw the HUD over the picture (off for clips that already contain it) */
  drawHud?: boolean;
  /** text shown in the sim view without video */
  simLabel?: string;
  /** text shown when the log time is outside the video */
  outsideLabel?: string;
}

export interface AnalyzeOptions {
  labeler?: Labeler | null;
  learned?: LabelExample[] | null;
}

export class FlightPlayer {
  readonly canvas: HTMLCanvasElement;
  readonly video: HTMLVideoElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly hud = new Hud();
  private readonly sim = new SimView();

  flight: FlightData | null = null;
  analysis: Analysis | null = null;
  track: Track | null = null;
  settings: DetectSettings;
  hudOptions: HudOptions;
  drawHud: boolean;
  /** user corrections of trick labels (key = trick start) */
  userLabels = new Map<string, TrickType>();

  /** videoTime = offset + speed · logTime */
  offset = 0;
  speed = 1;
  videoFile: Blob | null = null;
  videoUrl = '';
  clockT = 0;
  playing = false;
  rate = 1;
  /** replay mode: log time span covered without a decoded log */
  replayDuration = 0;

  simLabel: string;
  outsideLabel: string;
  /** called whenever playback starts or stops (also on video end) */
  onPlayingChange: ((playing: boolean) => void) | null = null;
  onEnded: (() => void) | null = null;

  constructor(canvas: HTMLCanvasElement, video: HTMLVideoElement, opts: PlayerOptions = {}) {
    this.canvas = canvas;
    this.video = video;
    this.ctx = canvas.getContext('2d')!;
    this.settings = opts.settings ?? { ...DEFAULT_SETTINGS };
    this.hudOptions = opts.hud ?? { ...DEFAULT_HUD };
    this.drawHud = opts.drawHud ?? true;
    this.simLabel = opts.simLabel ?? 'SIM-ANSICHT (kein Video)';
    this.outsideLabel = opts.outsideLabel ?? 'Außerhalb des Logs – Versatz prüfen';
    if (!canvas.width || canvas.width === 300) {
      canvas.width = 1280;
      canvas.height = 720;
    }
    video.addEventListener('ended', () => {
      this.setPlaying(false);
      this.onEnded?.();
    });
    video.addEventListener('pause', () => {
      if (this.playing && !video.ended) this.setPlaying(false);
    });
  }

  // ---------- time ----------
  get duration(): number {
    return this.flight?.info.duration ?? this.replayDuration;
  }

  /** a log or a replay clip is loaded */
  get ready(): boolean {
    return !!this.flight || (this.replayDuration > 0 && !!this.videoUrl);
  }

  hasVideo(): boolean {
    return !!this.videoUrl && this.video.readyState >= 1;
  }

  toVideoTime(t: number): number {
    return this.offset + this.speed * t;
  }

  toLogTime(v: number): number {
    return (v - this.offset) / this.speed;
  }

  logTime(): number {
    return this.hasVideo() ? this.toLogTime(this.video.currentTime) : this.clockT;
  }

  seek(t: number) {
    if (!this.ready) return;
    t = Math.max(0, Math.min(this.duration, t));
    if (this.hasVideo()) this.video.currentTime = Math.max(0, Math.min(this.video.duration || 0, this.toVideoTime(t)));
    this.clockT = t;
  }

  setPlaying(on: boolean) {
    if (!this.ready) return;
    const changed = this.playing !== on;
    this.playing = on;
    if (this.hasVideo()) {
      if (on) {
        this.video.play().catch(() => {
          this.playing = false;
          this.onPlayingChange?.(false);
        });
      } else this.video.pause();
    } else if (on && this.clockT >= this.duration - 0.01) {
      this.clockT = 0;
    }
    if (changed) this.onPlayingChange?.(on);
  }

  setRate(rate: number) {
    this.rate = rate;
    this.video.playbackRate = rate;
  }

  setSync(offset: number, speed = this.speed) {
    this.offset = Math.round(offset * 1000) / 1000;
    this.speed = Math.round(Math.min(1.1, Math.max(0.9, speed)) * 1e6) / 1e6;
  }

  /** Advance the clock without video (sim view). Returns true when the end was reached. */
  advance(dt: number): boolean {
    if (this.hasVideo() || !this.playing) return false;
    this.clockT += dt * this.rate;
    if (this.clockT >= this.duration) {
      this.clockT = this.duration;
      this.setPlaying(false);
      return true;
    }
    return false;
  }

  // ---------- data ----------
  setFlight(f: FlightData) {
    this.flight = f;
    this.replayDuration = 0;
    this.userLabels = new Map();
    this.track = computeTrack(f);
    this.clockT = 0;
    this.playing = false;
    this.analysis = null;
  }

  /** Replay a clip with burned-in HUD: the clip starts at log time `clipStart`, covers `duration` s of log. */
  setReplay(duration: number, clipStart: number) {
    this.flight = null;
    this.analysis = null;
    this.track = null;
    this.replayDuration = duration;
    this.drawHud = false;
    this.setSync(-clipStart, 1);
    this.clockT = 0;
    this.playing = false;
  }

  analyze(opts: AnalyzeOptions = {}): Analysis | null {
    if (!this.flight) return null;
    this.analysis = detectTricks(this.flight, this.settings, {
      labeler: opts.labeler ?? null,
      userLabels: this.userLabels,
      learned: opts.learned ?? null,
    });
    return this.analysis;
  }

  /** Load a video (file or URL). Resolves when the metadata is known. */
  loadVideo(src: Blob | string): Promise<void> {
    if (this.videoUrl && this.videoUrl.startsWith('blob:')) URL.revokeObjectURL(this.videoUrl);
    this.videoFile = typeof src === 'string' ? null : src;
    this.videoUrl = typeof src === 'string' ? src : URL.createObjectURL(src);
    this.video.src = this.videoUrl;
    this.video.playbackRate = this.rate;
    return new Promise((resolve, reject) => {
      const done = () => {
        const big = this.video.videoHeight >= 1000;
        this.canvas.width = big ? 1920 : 1280;
        this.canvas.height = big ? 1080 : 720;
        cleanup();
        resolve();
      };
      const fail = () => {
        cleanup();
        reject(new Error('Video kann in diesem Browser nicht abgespielt werden.'));
      };
      const cleanup = () => {
        this.video.removeEventListener('loadedmetadata', done);
        this.video.removeEventListener('error', fail);
      };
      this.video.addEventListener('loadedmetadata', done);
      this.video.addEventListener('error', fail);
    });
  }

  // ---------- rendering ----------
  /** Draw the frame for log time t (video or sim view, HUD on top). */
  render(t = this.logTime()) {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;
    const f = this.flight;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    if (!this.ready) return;
    const video = this.video;
    if (this.hasVideo() && video.videoWidth) {
      const k = Math.min(W / video.videoWidth, H / video.videoHeight);
      const vw = video.videoWidth * k, vh = video.videoHeight * k;
      ctx.drawImage(video, (W - vw) / 2, (H - vh) / 2, vw, vh);
      if (f && (t < 0 || t > this.duration)) {
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(0, H - 40, W, 40);
        ctx.fillStyle = '#fee440';
        ctx.font = '600 18px system-ui';
        ctx.fillText(this.outsideLabel, 20, H - 14);
      }
    } else if (f) {
      this.sim.draw(ctx, W, H, f, indexAt(f.t, t), this.track);
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      ctx.font = '600 14px system-ui';
      ctx.fillText(this.simLabel, 24, H - 128);
    }
    if (f && this.drawHud && this.analysis && t >= 0 && t <= this.duration + 0.05) {
      this.hud.draw(ctx, W, H, f, indexAt(f.t, t), t, this.analysis, this.settings.comboWindow, this.hudOptions);
    }
  }
}
