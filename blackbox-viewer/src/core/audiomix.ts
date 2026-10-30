// Ton & Musik für Aufnahmen: eine Playlist (mehrere Songs nacheinander, fest ins Video gemischt)
// und den Originalton des Videos mitnehmen, entfernen oder vom Video lösen und als eigene
// Tondatei speichern. Läuft über Web Audio: das Video-Element wird einmal an einen AudioContext
// gehängt; jede Aufnahme bekommt eine eigene Mischung (MediaStreamAudioDestinationNode).

export type OriginalMode = 'keep' | 'remove' | 'separate';

export interface AudioSettings {
  /** Originalton des Videos: mitaufnehmen, entfernen oder separat speichern */
  original: OriginalMode;
  /** Lautstärke des Originaltons in der Aufnahme (1 = unverändert) */
  originalVolume: number;
  /** Lautstärke der Musik (1 = unverändert) */
  musicVolume: number;
  /** ab dieser Stelle im ersten Song (s) beginnt die Musik */
  musicOffset: number;
  /** Playlist wiederholen, wenn die Musik kürzer als die Aufnahme ist */
  loop: boolean;
  /** Musik ein- und ausblenden */
  fade: boolean;
  /** die Mischung beim Abspielen im Viewer mithören */
  monitor: boolean;
}

export const DEFAULT_AUDIO: AudioSettings = {
  original: 'keep', originalVolume: 1, musicVolume: 0.8, musicOffset: 0, loop: true, fade: true, monitor: true,
};
export const FADE_IN = 1;
export const FADE_OUT = 2;
/** so weit (Sekunden Musik) im Voraus werden Songs eingeplant */
const HORIZON = 30;

export interface GainStep {
  /** Sekunden ab Aufnahmestart */
  at: number;
  value: number;
  /** linear dorthin blenden statt springen */
  ramp: boolean;
}

/** Lautstärkeverlauf der Musik für eine Aufnahme der Länge `length` (null = unbekannt). */
export function gainPlan(volume: number, length: number | null, fade: boolean): GainStep[] {
  if (!fade) return [{ at: 0, value: volume, ramp: false }];
  const fadeIn = length === null ? FADE_IN : Math.min(FADE_IN, length / 4);
  const steps: GainStep[] = [{ at: 0, value: 0, ramp: false }, { at: fadeIn, value: volume, ramp: true }];
  if (length !== null && length > fadeIn) {
    const fadeOut = Math.min(FADE_OUT, length / 4);
    steps.push({ at: Math.max(fadeIn, length - fadeOut), value: volume, ramp: false }, { at: length, value: 0, ramp: true });
  }
  return steps;
}

/** Startstelle im Song: mindestens 0, höchstens kurz vor dem Ende. */
export function clampOffset(offset: number, duration: number): number {
  const o = Number.isFinite(offset) ? offset : 0;
  return Math.max(0, Math.min(o, Math.max(0, duration - 0.5)));
}

export interface Segment {
  /** Song in der Playlist */
  index: number;
  /** Beginn relativ zur angefragten Musikposition (s) */
  at: number;
  /** Stelle im Song (s) */
  from: number;
  /** Länge bis zum Songende (s) */
  dur: number;
}

/**
 * Abfolge der Songs ab der Musikposition `pos` (Sekunden seit Musikbeginn) für mindestens
 * `span` Sekunden. Der erste Song beginnt bei `offset`, beim Wiederholen beginnen alle Songs
 * von vorn. Ohne Wiederholen endet die Liste nach dem letzten Song.
 */
export function playlistPlan(durations: number[], offset: number, pos: number, span: number, loop: boolean, max = 500): Segment[] {
  const n = durations.length;
  if (!n || durations.every((d) => !(d > 0))) return [];
  const out: Segment[] = [];
  const end = pos + span;
  let t = 0;
  let pass = 0;
  for (let i = 0; out.length < max; ) {
    const start = pass === 0 && i === 0 ? clampOffset(offset, durations[0]) : 0;
    const len = Math.max(0, (durations[i] || 0) - start);
    if (len > 0 && t + len > pos) {
      const skip = Math.max(0, pos - t);
      out.push({ index: i, at: t + skip - pos, from: start + skip, dur: len - skip });
      if (t + len >= end) break;
    }
    t += len;
    if (++i >= n) {
      if (!loop) break;
      i = 0;
      pass++;
    }
  }
  return out;
}

/** Braucht es die Mischung? Sonst nimmt die Aufnahme den Videoton wie bisher direkt. */
export function usesMixer(s: AudioSettings, hasMusic: boolean): boolean {
  return hasMusic || s.original !== 'keep' || Math.abs(s.originalVolume - 1) > 0.001;
}

/** Gespeicherte Einstellungen bereinigen (localStorage kann alles enthalten). */
export function sanitizeAudio(raw: unknown): AudioSettings {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Partial<Record<keyof AudioSettings, unknown>>;
  const vol = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(1.5, v)) : d);
  return {
    original: r.original === 'remove' || r.original === 'separate' ? r.original : 'keep',
    originalVolume: vol(r.originalVolume, DEFAULT_AUDIO.originalVolume),
    musicVolume: vol(r.musicVolume, DEFAULT_AUDIO.musicVolume),
    musicOffset: typeof r.musicOffset === 'number' && Number.isFinite(r.musicOffset) ? Math.max(0, Math.min(3600, r.musicOffset)) : 0,
    loop: typeof r.loop === 'boolean' ? r.loop : DEFAULT_AUDIO.loop,
    fade: typeof r.fade === 'boolean' ? r.fade : DEFAULT_AUDIO.fade,
    monitor: typeof r.monitor === 'boolean' ? r.monitor : DEFAULT_AUDIO.monitor,
  };
}

/** Ein Song der Playlist */
export interface Track {
  title: string;
  /** eigene Datei oder aus der Musik-Bibliothek des Servers (dann mit dessen id) */
  origin: 'file' | 'server';
  serverId?: string;
  buffer: AudioBuffer;
}

/** Ton einer laufenden Aufnahme */
export interface RecordingAudio {
  /** Tonspur fürs Video; null = Video ohne Ton */
  track: MediaStreamTrack | null;
  /** Originalton als eigener Stream (nur bei original = 'separate' und Wunsch des Aufrufers) */
  separate: MediaStream | null;
  pause(): void;
  resume(): void;
  stop(): void;
}

export const AUDIO_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];

/** Musikdatei dekodieren (ohne AudioContext: geht auch ohne Klick). */
export async function decodeMusic(data: ArrayBuffer, name: string): Promise<AudioBuffer> {
  let buffer: AudioBuffer;
  try {
    buffer = await new OfflineAudioContext(2, 1, 44100).decodeAudioData(data);
  } catch {
    throw new Error(`„${name}“ lässt sich nicht als Musik lesen (MP3, M4A, OGG, WAV oder FLAC verwenden).`);
  }
  if (buffer.duration < 1) throw new Error(`„${name}“ ist kürzer als eine Sekunde.`);
  return buffer;
}

export class AudioMixer {
  settings: AudioSettings;
  /** Playlist: die Songs laufen nacheinander */
  tracks: Track[] = [];
  private ctx: AudioContext | null = null;
  private source: MediaElementAudioSourceNode | null = null;
  /** Originalton zu den Lautsprechern */
  private monitorGain: GainNode | null = null;
  private preview: { stop: () => void; gain: GainNode; startedAt: number; rate: number } | null = null;
  private previewPos = 0;
  private recording = false;
  private video: HTMLVideoElement;

  constructor(video: HTMLVideoElement, settings: AudioSettings) {
    this.video = video;
    this.settings = settings;
  }

  /** Hängt das Video schon am AudioContext? Dann läuft sein Ton nur noch über die Mischung. */
  get active(): boolean {
    return this.ctx !== null;
  }

  get hasMusic(): boolean {
    return this.tracks.length > 0;
  }

  /** Gesamtlänge der Playlist (ein Durchgang, ab der Startstelle im ersten Song) */
  get musicDuration(): number {
    const d = this.tracks.reduce((sum, t) => sum + t.buffer.duration, 0);
    return this.tracks.length ? d - clampOffset(this.settings.musicOffset, this.tracks[0].buffer.duration) : 0;
  }

  /** Kurzname für Hinweise: der Titel oder die Anzahl Songs */
  get musicLabel(): string {
    return this.tracks.length === 1 ? this.tracks[0].title : this.tracks.length ? `${this.tracks.length} Songs` : '';
  }

  addTrack(track: Track) {
    this.tracks.push(track);
    this.playlistChanged();
  }

  removeTrack(index: number) {
    this.tracks.splice(index, 1);
    this.playlistChanged();
  }

  moveTrack(index: number, delta: number) {
    const to = index + delta;
    if (to < 0 || to >= this.tracks.length) return;
    const [t] = this.tracks.splice(index, 1);
    this.tracks.splice(to, 0, t);
    this.playlistChanged();
  }

  clearMusic() {
    this.tracks = [];
    this.playlistChanged();
  }

  /** Geänderte Playlist: die Vorschau beginnt wieder beim ersten Song */
  private playlistChanged() {
    this.stopPreview();
    this.previewPos = 0;
  }

  /** AudioContext und Video-Anschluss erst bei Bedarf (nach einem Klick) anlegen. */
  private ensure(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.source = this.ctx.createMediaElementSource(this.video);
      this.monitorGain = this.ctx.createGain();
      this.source.connect(this.monitorGain).connect(this.ctx.destination);
      this.applyMonitor();
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    return this.ctx;
  }

  /** Was man beim Abspielen vom Originalton hört: die Mischung oder (ohne Mithören) unverändert. */
  applyMonitor() {
    if (!this.monitorGain) return;
    const s = this.settings;
    this.monitorGain.gain.value = this.recording || s.monitor ? (s.original === 'keep' ? s.originalVolume : 0) : 1;
  }

  /** Wiedergabe startet (Klick): einen pausierten AudioContext wecken, Musik mithören. */
  onPlay(rate: number) {
    if (this.ctx) this.ensure();
    this.startPreview(rate);
  }

  onPause() {
    this.stopPreview(true);
  }

  /** Spulen oder Tempo ändern: Musik beginnt wie bei einer Aufnahme ab hier von vorn. */
  restart(playing: boolean, rate: number) {
    this.stopPreview();
    this.previewPos = 0;
    if (playing) this.onPlay(rate);
  }

  /**
   * Einstellungen oder Playlist geändert: Mithören anpassen. `replan`: die laufende Vorschau an
   * derselben Stelle neu einplanen (Startstelle, Wiederholen); sonst nur die Lautstärke.
   */
  refresh(playing: boolean, rate: number, replan = true) {
    // Mithören einer geänderten Mischung braucht den AudioContext (die Änderung ist ein Klick)
    if (this.settings.monitor && usesMixer(this.settings, this.hasMusic)) this.ensure();
    this.applyMonitor();
    if (this.preview && !replan && this.settings.monitor) {
      this.preview.gain.gain.value = this.settings.musicVolume;
      return;
    }
    this.stopPreview(true);
    if (playing) this.startPreview(rate);
  }

  /**
   * Playlist ab Musikposition `pos` in `out` abspielen; `startAt` = AudioContext-Zeit von `pos`.
   * Songs werden laufend für die nächsten 30 s eingeplant – so gehen auch lange Videos mit
   * vielen kurzen Songs. Liefert eine Funktion zum Anhalten.
   */
  private playMusic(ctx: AudioContext, out: AudioNode, startAt: number, pos: number, rate: number): () => void {
    const tracks = [...this.tracks];
    const durations = tracks.map((t) => t.buffer.duration);
    const offset = this.settings.musicOffset;
    const loop = this.settings.loop;
    const nodes = new Set<AudioBufferSourceNode>();
    let planned = pos; // bis hierhin (Musikzeit) ist schon eingeplant
    const plan = () => {
      const now = pos + Math.max(0, ctx.currentTime - startAt) * rate;
      if (planned >= now + HORIZON) return;
      const segments = playlistPlan(durations, offset, planned, now + HORIZON - planned, loop);
      if (!segments.length) {
        planned = Infinity;
        return;
      }
      for (const seg of segments) {
        const when = startAt + (planned - pos + seg.at) / rate;
        const late = Math.max(0, ctx.currentTime - when);
        if (seg.dur - late * rate <= 0.01) continue;
        const node = ctx.createBufferSource();
        node.buffer = tracks[seg.index].buffer;
        node.playbackRate.value = rate;
        node.connect(out);
        node.start(when + late, seg.from + late * rate, seg.dur - late * rate);
        node.onended = () => {
          nodes.delete(node);
          node.disconnect();
        };
        nodes.add(node);
      }
      const last = segments[segments.length - 1];
      planned += last.at + last.dur;
    };
    plan();
    const timer = window.setInterval(plan, 5000);
    return () => {
      clearInterval(timer);
      for (const node of nodes) {
        try {
          node.stop();
        } catch {
          /* schon beendet */
        }
        node.disconnect();
      }
      nodes.clear();
    };
  }

  private startPreview(rate: number) {
    if (!this.hasMusic || !this.settings.monitor || this.recording) return;
    const ctx = this.ensure();
    const gain = ctx.createGain();
    gain.gain.value = this.settings.musicVolume;
    gain.connect(ctx.destination);
    const startedAt = ctx.currentTime + 0.02;
    const stop = this.playMusic(ctx, gain, startedAt, this.previewPos, rate);
    this.preview = {
      stop: () => {
        stop();
        gain.disconnect();
      },
      gain, startedAt, rate,
    };
  }

  private stopPreview(keepPosition = false) {
    if (!this.preview) return;
    const p = this.preview;
    this.preview = null;
    if (keepPosition && this.ctx) this.previewPos += Math.max(0, this.ctx.currentTime - p.startedAt) * p.rate;
    p.stop();
  }

  /**
   * Ton für eine Aufnahme anlegen. `length` = erwartete Länge in Sekunden (für das Ausblenden;
   * null = unbekannt). `wantSeparate`: den Originalton zusätzlich als eigenen Stream liefern.
   * `plain`: nur der unveränderte Originalton, ohne Musik (Markierungen für den Admin).
   */
  beginRecording(length: number | null, wantSeparate: boolean, plain = false): RecordingAudio {
    const ctx = this.ensure();
    const s: AudioSettings = plain ? { ...this.settings, original: 'keep', originalVolume: 1 } : this.settings;
    const withMusic = !plain && this.hasMusic;
    const source = this.source!;
    this.stopPreview();
    this.recording = true;
    this.applyMonitor();
    const dest = ctx.createMediaStreamDestination();
    const original = ctx.createGain();
    original.gain.value = s.original === 'keep' ? s.originalVolume : 0;
    source.connect(original).connect(dest);
    let separate: MediaStreamAudioDestinationNode | null = null;
    if (s.original === 'separate' && wantSeparate) {
      separate = ctx.createMediaStreamDestination();
      source.connect(separate);
    }
    let gain: GainNode | null = null;
    let stopMusic: (() => void) | null = null;
    if (withMusic) {
      gain = ctx.createGain();
      gain.connect(dest);
      gain.connect(ctx.destination); // mithören, was aufgenommen wird
      const t0 = ctx.currentTime + 0.05;
      gain.gain.setValueAtTime(0, ctx.currentTime);
      for (const step of gainPlan(s.musicVolume, length, s.fade)) {
        if (step.ramp) gain.gain.linearRampToValueAtTime(step.value, t0 + step.at);
        else gain.gain.setValueAtTime(step.value, t0 + step.at);
      }
      stopMusic = this.playMusic(ctx, gain, t0, 0, 1);
    }
    const hasSound = s.original === 'keep' || withMusic;
    let stopped = false;
    return {
      track: hasSound ? dest.stream.getAudioTracks()[0] ?? null : null,
      separate: separate?.stream ?? null,
      pause: () => void ctx.suspend(),
      resume: () => void ctx.resume(),
      stop: () => {
        if (stopped) return;
        stopped = true;
        if (ctx.state === 'suspended') void ctx.resume();
        stopMusic?.();
        gain?.disconnect();
        source.disconnect(original);
        original.disconnect();
        if (separate) source.disconnect(separate);
        this.recording = false;
        this.applyMonitor();
      },
    };
  }
}
