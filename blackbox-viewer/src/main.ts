import './styles.css';
import { indexAt } from './analysis/filters.ts';
import { MIN_EXAMPLES, TrickClassifier } from './analysis/ml.ts';
import { attitudeAt } from './analysis/orientation.ts';
import {
  DEFAULT_SETTINGS, TRICK_COLOR, TRICK_LABEL, TRICK_TYPES,
  detectTricks, summarize, type Analysis, type DetectSettings,
} from './analysis/tricks.ts';
import { MspClient, serialSupported } from './decoder/msp.ts';
import { simulateFlight } from './demo/simulate.ts';
import { audioEnvelope, correlate, logSignal, throttlePunches } from './sync/audio.ts';
import type { FlightData, Trick, TrickType, WorkerRequest, WorkerResponse } from './types.ts';
import { DEFAULT_HUD, Hud, type HudOptions } from './ui/hud.ts';
import { SubmitRejectedError, fetchAuth, fetchLeaderboard, submitScore, type AuthState } from './ui/leaderboard.ts';
import { SimView, computeTrack, type Track } from './ui/simview.ts';
import { Timeline } from './ui/timeline.ts';

// ---------- helpers ----------
const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] => {
  const e = Object.assign(document.createElement(tag), props) as HTMLElementTagNameMap[K];
  e.append(...children);
  return e;
};
const fmtTime = (t: number) => {
  const s = Math.max(0, t);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, '0')}`;
};
function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}
function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}
function download(blob: Blob, name: string) {
  const a = el('a', { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}
let toastTimer = 0;
function toast(msg: string, kind: 'info' | 'error' = 'info', ms = 4000) {
  const t = $('toast');
  t.textContent = msg;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (t.hidden = true), ms);
}

// ---------- state ----------
const state = {
  flight: null as FlightData | null,
  analysis: null as Analysis | null,
  track: null as Track | null,
  settings: load<DetectSettings>('bba.settings', DEFAULT_SETTINGS),
  hud: load<HudOptions>('bba.hud', DEFAULT_HUD),
  userLabels: new Map<string, TrickType>(),
  offset: 0,
  videoFile: null as File | null,
  videoUrl: '',
  clockT: 0,
  playing: false,
  rate: 1,
  markVideo: null as number | null,
  markLog: null as number | null,
  activeTrickId: -1,
  showRejected: false,
};
const ml = new TrickClassifier();
const hud = new Hud();
const sim = new SimView();
const video = $<HTMLVideoElement>('video');
const stage = $<HTMLCanvasElement>('stageCanvas');
const sctx = stage.getContext('2d')!;
stage.width = 1280;
stage.height = 720;

const timeline = new Timeline($<HTMLCanvasElement>('timeline'), {
  onSeek: (t) => seek(t),
  onPick: (t) => {
    state.markLog = t;
    timeline.picking = false;
    timeline.setMarker(t);
    $('btnPickLog').textContent = 'In Timeline wählen';
    updateAnchorUi();
  },
});

// ---------- worker ----------
const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
const send = (msg: WorkerRequest, transfer: Transferable[] = []) => worker.postMessage(msg, transfer);
let logSizes: number[] = [];

worker.onmessage = (e: MessageEvent<WorkerResponse>) => {
  const msg = e.data;
  switch (msg.type) {
    case 'progress':
      busy(msg.label, msg.value);
      break;
    case 'logs': {
      logSizes = msg.logs.map((l) => l.bytes);
      const sel = $<HTMLSelectElement>('logSelect');
      sel.replaceChildren(...msg.logs.map((l) => el('option', { value: String(l.index), textContent: `Log ${l.index + 1} (${(l.bytes / 1024).toFixed(0)} KB)` })));
      $('logSelectRow').hidden = msg.logs.length < 2;
      // default: the largest log (usually the actual flight)
      const best = msg.logs.reduce((a, b) => (b.bytes > a.bytes ? b : a), msg.logs[0]);
      sel.value = String(best.index);
      if (msg.logs.length > 1) toast(`${msg.logs.length} Logs in der Datei gefunden – zeige Log ${best.index + 1}. Auswahl unter „Log-Info“.`);
      send({ type: 'process', index: best.index });
      break;
    }
    case 'flight':
      busy(null);
      setFlight(msg.flight);
      break;
    case 'csv':
      busy(null);
      download(msg.blob, msg.name);
      break;
    case 'error':
      busy(null);
      toast(msg.message, 'error', 7000);
      break;
  }
};

function busy(label: string | null, value = 0) {
  $('busy').hidden = label === null;
  if (label !== null) {
    $('busyLabel').textContent = label;
    $('busyBar').style.width = `${Math.round(value * 100)}%`;
  }
}

async function loadLogFile(file: File | Blob, name: string) {
  busy('Lese Datei…', 0.02);
  const buffer = await file.arrayBuffer();
  send({ type: 'load', name, buffer }, [buffer]);
}

// ---------- flight / analysis ----------
function setFlight(f: FlightData) {
  state.flight = f;
  state.userLabels = new Map();
  state.track = computeTrack(f);
  state.clockT = 0;
  state.playing = false;
  state.markLog = null;
  timeline.setMarker(null);
  $('emptyState').hidden = true;
  for (const id of ['btnPlay', 'btnBack', 'btnFwd', 'btnRec', 'btnExport']) $<HTMLButtonElement>(id).disabled = false;
  $('sourceChip').textContent = f.info.source === 'demo' ? 'Demo' : f.info.fileName;
  reanalyze(true);
  renderInfo();
  updateVideoUi();
  updateAnchorUi();
  updateSubmit();
  if (state.videoUrl) seek(Math.max(0, -state.offset));
  else seek(0);
}

function reanalyze(resetView = false) {
  const f = state.flight;
  if (!f) return;
  state.analysis = detectTricks(f, state.settings, {
    labeler: ml.ready ? (feat) => ml.predict(feat) : null,
    userLabels: state.userLabels,
  });
  timeline.setData(f, state.analysis, resetView);
  renderTricks();
  renderScore();
  renderMl();
  updateSubmit();
}

function renderScore() {
  const a = state.analysis;
  const s = summarize(a?.tricks ?? []);
  $('scoreTotal').textContent = s.total.toLocaleString('de-DE');
  $('statTricks').textContent = String(s.count);
  $('statCombo').textContent = s.maxCombo ? `${s.maxCombo}×` : '0';
  $('statBest').textContent = s.best ? s.best.name : '–';
  $('statBest').title = s.best ? `${s.best.name}: ${s.best.points} Punkte` : '';
}

function trickRow(tr: Trick, rejected: boolean) {
  const select = el('select', { title: 'Label korrigieren (wird als Trainingsbeispiel gespeichert)' });
  select.setAttribute('aria-label', 'Trick-Label korrigieren');
  select.append(...TRICK_TYPES.map((t) => el('option', { value: t, textContent: TRICK_LABEL[t], selected: t === tr.type })));
  select.addEventListener('click', (e) => e.stopPropagation());
  select.addEventListener('change', () => {
    const label = select.value as TrickType;
    state.userLabels.set(tr.key, label);
    ml.add(tr.features, label);
    reanalyze();
    toast(`Als „${TRICK_LABEL[label]}“ markiert – ${ml.examples.length} Trainingsbeispiele.`);
  });
  const badges: Node[] = [];
  if (tr.source === 'ml') badges.push(el('span', { className: 'badge ml', textContent: 'ML' }));
  if (tr.source === 'user') badges.push(el('span', { className: 'badge user', textContent: 'manuell' }));
  const li = el(
    'li',
    { className: rejected ? 'rejected' : '' },
    el('span', { className: 'swatch' }),
    el(
      'div',
      {},
      el('div', { className: 'name' }, rejected ? 'Rotation' : tr.name, ...badges, select),
      el('div', { className: 'meta', textContent: `${fmtTime(tr.start)} · ${tr.detail}` }),
    ),
    el(
      'div',
      { className: 'pts' },
      rejected ? '–' : `${tr.points}`,
      ...(tr.comboLevel > 0 && !rejected ? [el('small', { textContent: `COMBO ×${tr.comboMult.toFixed(2)}` })] : []),
    ),
  );
  (li.firstChild as HTMLElement).style.background = TRICK_COLOR[tr.type];
  li.dataset.id = rejected ? '' : String(tr.id);
  li.addEventListener('click', () => seek(Math.max(0, tr.start - 0.8)));
  return li;
}

function renderTricks() {
  const list = $('trickList');
  const a = state.analysis;
  if (!a) return list.replaceChildren();
  const rows = [...a.tricks.map((t) => ({ t, rejected: false })), ...(state.showRejected ? a.rejected.map((t) => ({ t, rejected: true })) : [])];
  rows.sort((x, y) => x.t.start - y.t.start);
  list.replaceChildren(...rows.map(({ t, rejected }) => trickRow(t, rejected)));
  if (!rows.length) list.append(el('li', { className: 'empty-list', textContent: 'Keine Tricks erkannt. Schwellwerte unter „Erkennung“ anpassen.' }));
  state.activeTrickId = -1;
}

function highlightActive(t: number) {
  const a = state.analysis;
  if (!a) return;
  const cur = a.tricks.find((tr) => t >= tr.start - 0.05 && t <= tr.end + 1.5);
  const id = cur ? cur.id : -1;
  if (id === state.activeTrickId) return;
  state.activeTrickId = id;
  for (const li of $('trickList').children) {
    const on = (li as HTMLElement).dataset.id === String(id);
    li.classList.toggle('active', on);
    if (on && state.playing) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

// ---------- playback ----------
const hasVideo = () => !!state.videoUrl && video.readyState >= 1;
const duration = () => state.flight?.info.duration ?? 0;

function logTime() {
  return hasVideo() ? video.currentTime - state.offset : state.clockT;
}

function seek(t: number) {
  if (!state.flight) return;
  t = Math.max(0, Math.min(duration(), t));
  if (hasVideo()) video.currentTime = Math.max(0, Math.min(video.duration || 0, t + state.offset));
  state.clockT = t;
}

function setPlaying(on: boolean) {
  if (!state.flight) return;
  state.playing = on;
  if (hasVideo()) {
    if (on) video.play().catch(() => (state.playing = false));
    else video.pause();
  } else if (on && state.clockT >= duration() - 0.01) {
    state.clockT = 0;
  }
  $('btnPlay').textContent = on ? '❚❚' : '▶';
}

video.addEventListener('ended', () => {
  setPlaying(false);
  if (recorder) stopRecording();
});
video.addEventListener('pause', () => {
  if (state.playing && !video.ended) setPlaying(false);
});

let lastNow = performance.now();
let lastLabel = '';
function frame(now: number) {
  requestAnimationFrame(frame);
  const dt = (now - lastNow) / 1000;
  lastNow = now;
  const f = state.flight;
  const W = stage.width, H = stage.height;
  if (!f) {
    sctx.fillStyle = '#000';
    sctx.fillRect(0, 0, W, H);
    return;
  }
  if (!hasVideo() && state.playing) {
    state.clockT += dt * state.rate;
    if (state.clockT >= duration()) {
      state.clockT = duration();
      setPlaying(false);
      if (recorder) stopRecording();
    }
  }
  const t = logTime();
  const i = indexAt(f.t, t);
  if (hasVideo() && video.videoWidth) {
    sctx.fillStyle = '#000';
    sctx.fillRect(0, 0, W, H);
    const k = Math.min(W / video.videoWidth, H / video.videoHeight);
    const vw = video.videoWidth * k, vh = video.videoHeight * k;
    sctx.drawImage(video, (W - vw) / 2, (H - vh) / 2, vw, vh);
    if (t < 0 || t > duration()) {
      sctx.fillStyle = 'rgba(0,0,0,0.5)';
      sctx.fillRect(0, H - 40, W, 40);
      sctx.fillStyle = '#fee440';
      sctx.font = '600 18px system-ui';
      sctx.fillText('Außerhalb des Logs – Versatz prüfen', 20, H - 14);
    }
  } else {
    sim.draw(sctx, W, H, f, i, state.track);
    sctx.fillStyle = 'rgba(255,255,255,0.55)';
    sctx.font = '600 14px system-ui';
    sctx.fillText('SIM-ANSICHT (kein Video)', 24, H - 128);
  }
  if (t >= 0 && t <= duration() + 0.05 && state.analysis) {
    hud.draw(sctx, W, H, f, i, t, state.analysis, state.settings.comboWindow, state.hud);
  }
  timeline.setCursor(t);
  const label = `${fmtTime(t)} / ${fmtTime(duration())}`;
  if (label !== lastLabel) {
    $('timeLabel').textContent = label;
    lastLabel = label;
  }
  highlightActive(t);
}
requestAnimationFrame(frame);

$('btnPlay').addEventListener('click', () => setPlaying(!state.playing));
$('btnBack').addEventListener('click', () => seek(logTime() - 2));
$('btnFwd').addEventListener('click', () => seek(logTime() + 2));
$<HTMLSelectElement>('speed').addEventListener('change', (e) => {
  state.rate = Number((e.target as HTMLSelectElement).value);
  video.playbackRate = state.rate;
});
document.addEventListener('keydown', (e) => {
  const tag = (e.target as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || !state.flight) return;
  if (e.code === 'Space') {
    e.preventDefault();
    setPlaying(!state.playing);
  } else if (e.code === 'ArrowLeft') seek(logTime() - (e.shiftKey ? 0.1 : 2));
  else if (e.code === 'ArrowRight') seek(logTime() + (e.shiftKey ? 0.1 : 2));
});

// ---------- HUD toggles ----------
const HUD_LABELS: Record<keyof HudOptions, string> = {
  score: 'Score & Trick-Feed',
  popups: 'Trick-Einblendungen',
  gauge: 'Rotations-Anzeige',
  sticks: 'Stick-Positionen',
  telemetry: 'Gas & Drehraten',
  attitude: 'Lage (3D-Quad)',
};
$('hudToggles').append(
  ...(Object.keys(HUD_LABELS) as (keyof HudOptions)[]).map((k) => {
    const cb = el('input', { type: 'checkbox', checked: state.hud[k] });
    cb.addEventListener('change', () => {
      state.hud[k] = cb.checked;
      store('bba.hud', state.hud);
    });
    return el('label', { className: 'check' }, cb, HUD_LABELS[k]);
  }),
);

// ---------- files & drag/drop ----------
function handleFiles(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    if (file.type.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi)$/i.test(file.name)) loadVideo(file);
    else loadLogFile(file, file.name);
  }
}
for (const id of ['fileLog', 'fileLog2', 'fileVideo']) {
  $<HTMLInputElement>(id).addEventListener('change', (e) => {
    const input = e.target as HTMLInputElement;
    if (input.files) handleFiles(input.files);
    input.value = '';
  });
}
let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepth++;
  $('dropHint').hidden = false;
});
window.addEventListener('dragleave', () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    $('dropHint').hidden = true;
  }
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  $('dropHint').hidden = true;
  if (e.dataTransfer?.files.length) handleFiles(e.dataTransfer.files);
});
const startDemo = () => {
  busy('Simuliere Demo-Flug…', 0.5);
  setTimeout(() => {
    try {
      setFlight(simulateFlight());
      toast('Demo-Flug geladen – ▶ drücken oder Leertaste.');
    } finally {
      busy(null);
    }
  }, 30);
};
$('btnDemo').addEventListener('click', startDemo);
$('btnDemo2').addEventListener('click', startDemo);
$<HTMLSelectElement>('logSelect').addEventListener('change', (e) => {
  busy('Dekodiere…', 0);
  send({ type: 'process', index: Number((e.target as HTMLSelectElement).value) });
});

// ---------- video & sync ----------
function loadVideo(file: File) {
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
  state.videoFile = file;
  state.videoUrl = URL.createObjectURL(file);
  video.src = state.videoUrl;
  video.playbackRate = state.rate;
  video.addEventListener(
    'loadedmetadata',
    () => {
      const big = video.videoHeight >= 1000;
      stage.width = big ? 1920 : 1280;
      stage.height = big ? 1080 : 720;
      updateVideoUi();
      seek(logTime());
      toast(`Video geladen: ${file.name}`);
    },
    { once: true },
  );
  video.addEventListener('error', () => toast('Video kann in diesem Browser nicht abgespielt werden.', 'error'), { once: true });
}

function setOffset(v: number) {
  state.offset = Math.round(v * 1000) / 1000;
  $<HTMLInputElement>('offset').value = state.offset.toFixed(3);
  updateVideoUi();
}

function updateVideoUi() {
  const has = !!state.videoFile;
  $('videoStatus').textContent = has
    ? `Video: ${state.videoFile!.name}${video.duration ? ` (${fmtTime(video.duration)})` : ''} – Log ${state.offset >= 0 ? 'startet' : 'beginnt'} bei Videozeit ${state.offset.toFixed(2)} s.`
    : 'Kein Video geladen. Ohne Video zeigt die Bühne eine synthetische FPV-Ansicht aus den Lagedaten.';
  $<HTMLButtonElement>('btnAutoSync').disabled = !(has && state.flight);
  $<HTMLButtonElement>('btnMarkVideo').disabled = !has;
  timeline.setVideoSpan(has && video.duration ? [-state.offset, video.duration - state.offset] : null);
}

$<HTMLInputElement>('offset').addEventListener('change', (e) => setOffset(Number((e.target as HTMLInputElement).value) || 0));
document.querySelectorAll<HTMLButtonElement>('[data-nudge]').forEach((b) =>
  b.addEventListener('click', () => setOffset(state.offset + Number(b.dataset.nudge))),
);

$('btnAutoSync').addEventListener('click', async () => {
  const f = state.flight;
  if (!f || !state.videoFile) return;
  const btn = $<HTMLButtonElement>('btnAutoSync');
  btn.disabled = true;
  const out = $('syncResult');
  try {
    if (state.videoFile.size > 1.5e9) throw new Error('Video > 1,5 GB – bitte Anker-Methode verwenden.');
    const env = await audioEnvelope(state.videoFile, (m) => (out.textContent = m));
    const res = correlate(env, logSignal(f));
    setOffset(res.offset);
    const sure = res.confidence >= 5;
    out.textContent = `Versatz ${res.offset.toFixed(2)} s · Konfidenz ${res.confidence.toFixed(1)}σ ${sure ? '✓' : '– unsicher, bitte prüfen / Anker verwenden'}`;
    drawSyncCurve(res.curve, res.curveStart, res.curveStep, res.offset);
    seek(logTime());
  } catch (err) {
    out.textContent = '';
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
  } finally {
    btn.disabled = false;
  }
});

function drawSyncCurve(curve: Float32Array, start: number, step: number, best: number) {
  const c = $<HTMLCanvasElement>('syncCurve');
  c.hidden = false;
  const w = (c.width = c.clientWidth * devicePixelRatio);
  const h = (c.height = c.clientHeight * devicePixelRatio);
  const g = c.getContext('2d')!;
  let mn = Infinity, mx = -Infinity;
  for (const v of curve) {
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  g.strokeStyle = '#58f0a8';
  g.beginPath();
  for (let x = 0; x < w; x++) {
    const k = Math.floor((x / w) * curve.length);
    const y = h - ((curve[k] - mn) / (mx - mn || 1)) * (h - 4) - 2;
    if (x) g.lineTo(x, y);
    else g.moveTo(x, y);
  }
  g.stroke();
  const bx = ((best - start) / step / curve.length) * w;
  g.strokeStyle = '#fee440';
  g.beginPath();
  g.moveTo(bx, 0);
  g.lineTo(bx, h);
  g.stroke();
}

function updateAnchorUi() {
  const f = state.flight;
  $('markVideo').textContent = state.markVideo === null ? '–' : `Video ${state.markVideo.toFixed(3)} s`;
  $('markLog').textContent = state.markLog === null ? '–' : `Log ${state.markLog.toFixed(3)} s`;
  $<HTMLButtonElement>('btnPickLog').disabled = !f;
  $<HTMLButtonElement>('btnSnap').disabled = !f;
  $<HTMLButtonElement>('btnBeep').disabled = !f?.events.some((e) => e.type === 'sync_beep');
  $<HTMLButtonElement>('btnApplyAnchor').disabled = state.markVideo === null || state.markLog === null;
}
$('btnMarkVideo').addEventListener('click', () => {
  state.markVideo = video.currentTime;
  updateAnchorUi();
});
$('btnPickLog').addEventListener('click', () => {
  timeline.picking = !timeline.picking;
  $('btnPickLog').textContent = timeline.picking ? 'Klick in die Timeline…' : 'In Timeline wählen';
});
$('btnBeep').addEventListener('click', () => {
  const beep = state.flight?.events.find((e) => e.type === 'sync_beep');
  if (!beep) return;
  state.markLog = beep.time;
  timeline.setMarker(beep.time);
  updateAnchorUi();
});
$('btnSnap').addEventListener('click', () => {
  const f = state.flight;
  if (!f) return;
  const punches = throttlePunches(f);
  if (!punches.length) return toast('Keine deutlichen Gas-Stöße im Log gefunden.', 'error');
  const ref = state.markLog ?? logTime();
  const best = punches.reduce((a, b) => (Math.abs(b - ref) < Math.abs(a - ref) ? b : a));
  state.markLog = best;
  timeline.setMarker(best);
  updateAnchorUi();
});
$('btnApplyAnchor').addEventListener('click', () => {
  if (state.markVideo === null || state.markLog === null) return;
  setOffset(state.markVideo - state.markLog);
  toast(`Versatz gesetzt: ${state.offset.toFixed(3)} s`);
});

// ---------- detection settings ----------
interface SettingDef {
  key: keyof DetectSettings;
  label: string;
  min: number;
  max: number;
  step: number;
  fmt: (v: number) => string;
  desc: string;
}
const deg = (v: number) => `${Math.round(v)}°/s`;
const sec = (v: number) => `${v.toFixed(2)} s`;
const SETTING_DEFS: SettingDef[] = [
  { key: 'startRate', label: 'Start-Drehrate', min: 150, max: 900, step: 10, fmt: deg, desc: 'Spitzenrate, ab der eine Rotation zählt' },
  { key: 'endRate', label: 'End-Drehrate', min: 20, max: 200, step: 5, fmt: deg, desc: 'Rotation endet, wenn die Rate darunter fällt' },
  { key: 'smoothHz', label: 'Gyro-Glättung', min: 3, max: 40, step: 1, fmt: (v) => `${v} Hz`, desc: 'Tiefpass vor der Segmentierung' },
  { key: 'fullTol', label: 'Winkel-Toleranz', min: 0.03, max: 0.35, step: 0.01, fmt: (v) => `${Math.round(v * 360)}°`, desc: 'So viel darf an 360° fehlen' },
  { key: 'halfMin', label: 'Halbe Rotation min.', min: 90, max: 180, step: 5, fmt: (v) => `${v}°`, desc: 'Für Split-S / Immelmann' },
  { key: 'halfMax', label: 'Halbe Rotation max.', min: 180, max: 300, step: 5, fmt: (v) => `${v}°`, desc: 'Für Split-S / Immelmann' },
  { key: 'maxSpinTime', label: 'Max. Dauer pro 360°', min: 0.4, max: 3, step: 0.05, fmt: sec, desc: 'Langsamere Drehungen sind Carves, keine Tricks' },
  { key: 'plMinDuration', label: 'Power Loop: min. Dauer', min: 0.4, max: 3, step: 0.05, fmt: sec, desc: 'Kürzere Pitch-Rotationen gelten als Flip' },
  { key: 'plMinThrottle', label: 'Power Loop: min. Gas', min: 0.1, max: 0.9, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, desc: 'Durchschnittsgas während des Loops' },
  { key: 'yawMinPeak', label: 'Yaw Spin: min. Rate', min: 200, max: 1500, step: 10, fmt: deg, desc: 'Trennt Spins von Orbits' },
  { key: 'seqGap', label: 'Split-S: max. Pause', min: 0.1, max: 1.5, step: 0.05, fmt: sec, desc: 'Zwischen halber Rolle und halbem Loop' },
  { key: 'comboWindow', label: 'Combo-Fenster', min: 0.3, max: 4, step: 0.1, fmt: sec, desc: 'Nächster Trick innerhalb → Combo-Multiplikator' },
];
let reanalyzeTimer = 0;
function renderSettings() {
  $('settings').replaceChildren(
    ...SETTING_DEFS.map((d) => {
      const out = el('output', { textContent: d.fmt(state.settings[d.key]) });
      const input = el('input', { type: 'range', min: String(d.min), max: String(d.max), step: String(d.step), value: String(state.settings[d.key]) });
      input.setAttribute('aria-label', d.label);
      input.addEventListener('input', () => {
        state.settings[d.key] = Number(input.value);
        out.textContent = d.fmt(state.settings[d.key]);
        store('bba.settings', state.settings);
        clearTimeout(reanalyzeTimer);
        reanalyzeTimer = window.setTimeout(() => reanalyze(), 120);
      });
      return el('div', { className: 'setting' }, el('span', { textContent: d.label }), out, input, el('span', { className: 'desc', textContent: d.desc }));
    }),
  );
}
renderSettings();
$('btnResetSettings').addEventListener('click', () => {
  state.settings = { ...DEFAULT_SETTINGS };
  store('bba.settings', state.settings);
  renderSettings();
  reanalyze();
});
$<HTMLInputElement>('showRejected').addEventListener('change', (e) => {
  state.showRejected = (e.target as HTMLInputElement).checked;
  renderTricks();
});

// ---------- ML ----------
function renderMl() {
  $<HTMLInputElement>('mlEnabled').checked = ml.enabled;
  const counts = ml.counts();
  const n = ml.examples.length;
  const status = ml.enabled
    ? n >= MIN_EXAMPLES ? 'aktiv' : `wartet auf ${MIN_EXAMPLES - n} weitere Beispiele`
    : 'aus';
  $('mlStats').replaceChildren(
    el('span', { textContent: `${n} Beispiele · Modell ${status}` }),
    ...(Object.entries(counts) as [TrickType, number][]).map(([k, v]) => {
      const s = el('span', { textContent: `${TRICK_LABEL[k]}: ${v}` });
      s.style.borderLeft = `3px solid ${TRICK_COLOR[k]}`;
      return s;
    }),
  );
}
$<HTMLInputElement>('mlEnabled').addEventListener('change', (e) => {
  ml.enabled = (e.target as HTMLInputElement).checked;
  ml.save();
  reanalyze();
  renderMl();
});
$('btnMlExport').addEventListener('click', () => download(new Blob([ml.exportJson()], { type: 'application/json' }), 'trick-training.json'));
$<HTMLInputElement>('mlImport').addEventListener('change', async (e) => {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    const n = ml.importJson(await file.text());
    toast(`${n} Trainingsbeispiele importiert.`);
    reanalyze();
    renderMl();
  } catch {
    toast('Datei ist keine gültige Trainingsdatei.', 'error');
  }
});
$('btnMlClear').addEventListener('click', () => {
  if (!confirm('Alle Trainingsbeispiele löschen?')) return;
  ml.clear();
  reanalyze();
  renderMl();
});
renderMl();

// ---------- tabs ----------
document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((btn) =>
  btn.addEventListener('click', () => {
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b === btn)));
    document.querySelectorAll<HTMLElement>('[data-panel]').forEach((p) => (p.hidden = p.dataset.panel !== btn.dataset.tab));
    if (btn.dataset.tab === 'board') refreshBoard();
  }),
);

// ---------- info ----------
function renderInfo() {
  const f = state.flight;
  if (!f) return;
  const i = f.info;
  const rows: [string, string][] = [
    ['Datei', i.fileName],
    ['Quelle', i.source === 'bbl' ? 'Blackbox binär (.bbl/.bfl)' : i.source === 'csv' ? 'CSV' : 'Demo'],
    ['Firmware', i.firmware],
    ['Craft', i.craft || '–'],
    ['Dauer', fmtTime(i.duration)],
    ['Frames', `${i.frames.toLocaleString('de-DE')} (${Math.round(i.srcRate)} Hz → Analyse ${Math.round(f.rate)} Hz)`],
    ['Felder', `${i.fields.length}: ${i.fields.slice(0, 14).join(', ')}${i.fields.length > 14 ? ' …' : ''}`],
    ['Beschleunigungssensor', f.acc ? 'ja (für Lageschätzung genutzt)' : 'nein (Lage nur aus Gyro)'],
    ['Events', f.events.length ? f.events.map((e) => `${e.type}@${e.time.toFixed(2)}s`).slice(0, 8).join(', ') : '–'],
    ['Korrupte Frames', String(i.corruptFrames)],
  ];
  if (i.logCount > 1) rows.splice(1, 0, ['Log', `${i.logIndex + 1} von ${i.logCount}`]);
  $('infoList').replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { textContent: k }), el('dd', { textContent: v })]));
  $('headerDump').textContent = Object.entries(i.headers).map(([k, v]) => `${k}: ${v}`).join('\n') || '–';
}

// ---------- export ----------
const exportBtn = $<HTMLButtonElement>('btnExport');
const exportMenu = $('exportMenu');
exportBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  exportMenu.hidden = !exportMenu.hidden;
  exportBtn.setAttribute('aria-expanded', String(!exportMenu.hidden));
});
document.addEventListener('click', () => {
  exportMenu.hidden = true;
  exportBtn.setAttribute('aria-expanded', 'false');
});
function trickJson(t: Trick) {
  return {
    type: t.type, name: t.name, count: t.count,
    start: +t.start.toFixed(3), end: +t.end.toFixed(3), duration: +t.duration.toFixed(3),
    angles: t.angles.map((a) => Math.round(a)), peakRate: Math.round(t.peakRate),
    meanThrottle: +t.meanThrottle.toFixed(3), purity: +t.purity.toFixed(3),
    basePoints: t.basePoints, comboMultiplier: t.comboMult, points: t.points, source: t.source,
  };
}
exportMenu.addEventListener('click', (e) => {
  const kind = (e.target as HTMLElement).dataset.export;
  const f = state.flight;
  if (!kind || !f) return;
  const base = f.info.fileName.replace(/\.[^.]+$/, '');
  const tricks = state.analysis?.tricks ?? [];
  if (kind === 'csv') {
    if (f.info.source === 'demo') return toast('Der Demo-Flug hat keine Rohdaten – JSON-Export verwenden.');
    busy('Exportiere CSV…', 0);
    send({ type: 'csv' });
  } else if (kind === 'tricks') {
    const data = { file: f.info.fileName, score: summarize(tricks).total, settings: state.settings, tricks: tricks.map(trickJson) };
    download(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), `${base}.tricks.json`);
  } else if (kind === 'json') {
    const r = (a: ArrayLike<number>, d = 2) => Array.from(a, (v) => +v.toFixed(d));
    const roll: number[] = [], pitch: number[] = [];
    for (let i = 0; i < f.t.length; i++) {
      const a = attitudeAt(f.quat, i);
      roll.push(+a.roll.toFixed(1));
      pitch.push(+a.pitch.toFixed(1));
    }
    const data = {
      info: { ...f.info, headers: undefined },
      sampleRate: f.rate,
      events: f.events,
      columns: {
        time: r(f.t, 4),
        gyroRoll: r(f.gyro[0], 1), gyroPitch: r(f.gyro[1], 1), gyroYaw: r(f.gyro[2], 1),
        throttle: r(f.throttle, 3),
        stickRoll: r(f.sticks[0], 3), stickPitch: r(f.sticks[1], 3), stickYaw: r(f.sticks[2], 3),
        motorAvg: f.motor ? r(f.motor, 3) : null,
        attitudeRoll: roll, attitudePitch: pitch,
      },
      tricks: tricks.map(trickJson),
    };
    download(new Blob([JSON.stringify(data)], { type: 'application/json' }), `${base}.flight.json`);
  }
});

// ---------- recording ----------
let recorder: MediaRecorder | null = null;
let recChunks: Blob[] = [];
function startRecording() {
  if (!state.flight || typeof MediaRecorder === 'undefined') return toast('Aufnahme wird von diesem Browser nicht unterstützt.', 'error');
  const stream = stage.captureStream(30);
  if (hasVideo()) {
    const v = video as HTMLVideoElement & { captureStream?: () => MediaStream; mozCaptureStream?: () => MediaStream };
    const vs = v.captureStream?.() ?? v.mozCaptureStream?.();
    vs?.getAudioTracks().forEach((tr) => stream.addTrack(tr));
  }
  const mime = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find((m) => MediaRecorder.isTypeSupported(m)) ?? '';
  recorder = new MediaRecorder(stream, { mimeType: mime || undefined, videoBitsPerSecond: 8_000_000 });
  recChunks = [];
  recorder.ondataavailable = (e) => e.data.size && recChunks.push(e.data);
  recorder.onstop = () => {
    const base = state.flight?.info.fileName.replace(/\.[^.]+$/, '') ?? 'flight';
    download(new Blob(recChunks, { type: 'video/webm' }), `${base}.hud.webm`);
    toast('Aufnahme gespeichert.');
  };
  recorder.start(1000);
  $('btnRec').classList.add('active');
  $('btnRec').textContent = '■ Stopp';
  if (!state.playing) setPlaying(true);
}
function stopRecording() {
  if (recorder?.state === 'recording') recorder.stop();
  recorder = null;
  $('btnRec').classList.remove('active');
  $('btnRec').textContent = '● Aufnehmen';
}
$('btnRec').addEventListener('click', () => (recorder ? stopRecording() : startRecording()));

// ---------- leaderboard ----------
const pilotInput = $<HTMLInputElement>('pilotName');
pilotInput.value = localStorage.getItem('bba.pilot') ?? '';
// Login requirement for submitting (set by the server, see server/leaderboard.mjs).
let auth: AuthState = { required: false, loggedIn: true, loginUrl: '' };
const loginBlocked = () => auth.required && !auth.loggedIn;
function showAuth() {
  $('loginHint').hidden = !loginBlocked();
  $<HTMLAnchorElement>('loginLink').href = auth.loginUrl || '/login';
  updateSubmit();
}
async function refreshAuth() {
  auth = await fetchAuth();
  showAuth();
}
// Demo flights are synthetic and must not end up in the leaderboard.
const isDemoFlight = () => state.flight?.info.source === 'demo';
function updateSubmit() {
  $('demoHint').hidden = !isDemoFlight();
  $<HTMLButtonElement>('btnSubmit').disabled =
    loginBlocked() || isDemoFlight() || !state.flight || !pilotInput.value.trim() || !(state.analysis?.tricks.length);
}
pilotInput.addEventListener('input', () => {
  try {
    localStorage.setItem('bba.pilot', pilotInput.value.trim());
  } catch {
    /* ignore */
  }
  updateSubmit();
});
$('submitForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = state.flight;
  if (!f || !state.analysis) return;
  if (f.info.source === 'demo') return toast('Demo-Flüge können nicht eingereicht werden.', 'error');
  const s = summarize(state.analysis.tricks);
  try {
    const res = await submitScore({
      source: f.info.source,
      pilot: pilotInput.value.trim(),
      score: s.total,
      tricks: s.count,
      best: s.best?.name ?? '',
      maxCombo: s.maxCombo,
      craft: f.info.craft,
      duration: f.info.duration,
    });
    toast(`Eingereicht – Platz ${res.rank}${res.online ? '' : ' (lokal gespeichert, kein Server erreichbar)'}.`);
  } catch (err) {
    if (!(err instanceof SubmitRejectedError)) throw err;
    if (err.loginRequired) {
      // Session expired or logged out in another tab.
      auth = { required: true, loggedIn: false, loginUrl: err.loginUrl || auth.loginUrl };
      showAuth();
    }
    toast(err.message, 'error');
    return;
  }
  refreshBoard();
});
// Re-check after returning to the tab, e.g. after logging in in another tab.
window.addEventListener('focus', () => void refreshAuth());
void refreshAuth();
async function refreshBoard() {
  const { entries, online } = await fetchLeaderboard();
  $('boardMode').textContent = online ? 'Online-Leaderboard' : 'Lokales Leaderboard (Server nicht erreichbar)';
  const me = pilotInput.value.trim().toLowerCase();
  $('boardBody').replaceChildren(
    ...(entries.length
      ? entries.map((e, k) =>
          el(
            'tr',
            { className: e.pilot.toLowerCase() === me ? 'me' : '' },
            el('td', { textContent: String(k + 1) }),
            el('td', { textContent: e.pilot }),
            el('td', { textContent: e.score.toLocaleString('de-DE') }),
            el('td', { textContent: String(e.tricks) }),
            el('td', { textContent: e.best || '–' }),
            el('td', { textContent: e.maxCombo ? `${e.maxCombo}×` : '–' }),
            el('td', { textContent: e.createdAt ? new Date(e.createdAt).toLocaleDateString('de-DE') : '' }),
          ),
        )
      : [el('tr', {}, el('td', { colSpan: 7, className: 'muted', textContent: 'Noch keine Einträge.' }))]),
  );
}
$('btnBoardRefresh').addEventListener('click', refreshBoard);

// ---------- USB / MSP ----------
const usbDialog = $<HTMLDialogElement>('usbDialog');
let msp: MspClient | null = null;
let usbData: Uint8Array | null = null;
let usbAbort: AbortController | null = null;
const usbStatus = (msg: string) => ($('usbStatus').textContent = msg);
$('btnUsb').addEventListener('click', () => {
  if (!serialSupported()) usbStatus('Web Serial wird von diesem Browser nicht unterstützt. Bitte Chrome oder Edge (Desktop) verwenden.');
  usbDialog.showModal();
});
$('usbConnect').addEventListener('click', async () => {
  try {
    if (msp) {
      await msp.disconnect();
      msp = null;
      $('usbConnect').textContent = 'Verbinden';
      $<HTMLButtonElement>('usbDownload').disabled = true;
      return usbStatus('Getrennt.');
    }
    msp = new MspClient();
    await msp.connect();
    const id = await msp.identify();
    const sum = await msp.flashSummary();
    $('usbConnect').textContent = 'Trennen';
    if (!sum.supported) {
      const sd = await msp.hasSdCard();
      return usbStatus(`${id} verbunden. Kein Onboard-Flash${sd ? ' – SD-Karte erkannt: per Mass-Storage auslesen.' : '.'}`);
    }
    usbStatus(`${id} verbunden. Flash: ${(sum.usedSize / 1048576).toFixed(2)} MB von ${(sum.totalSize / 1048576).toFixed(1)} MB belegt.`);
    const dl = $<HTMLButtonElement>('usbDownload');
    dl.disabled = sum.usedSize === 0;
    dl.dataset.size = String(sum.usedSize);
  } catch (err) {
    usbStatus(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    await msp?.disconnect();
    msp = null;
    $('usbConnect').textContent = 'Verbinden';
  }
});
$('usbDownload').addEventListener('click', async () => {
  if (!msp) return;
  const btn = $<HTMLButtonElement>('usbDownload');
  if (usbAbort) {
    usbAbort.abort();
    return;
  }
  const size = Number(btn.dataset.size);
  usbAbort = new AbortController();
  btn.textContent = 'Abbrechen';
  const t0 = performance.now();
  try {
    usbData = await msp.readFlash(
      size,
      (n) => {
        $('usbBar').style.width = `${(n / size) * 100}%`;
        const kbps = n / 1024 / ((performance.now() - t0) / 1000);
        usbStatus(`Lese Flash… ${(n / 1048576).toFixed(2)} / ${(size / 1048576).toFixed(2)} MB (${kbps.toFixed(0)} KB/s)`);
      },
      usbAbort.signal,
    );
    usbStatus('Download abgeschlossen. Log wird analysiert…');
    $<HTMLButtonElement>('usbSave').disabled = false;
    const copy = usbData.slice();
    loadLogFile(new Blob([copy]), `fc-flash-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.bbl`);
  } catch (err) {
    usbStatus(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    usbAbort = null;
    btn.textContent = 'Flash herunterladen';
  }
});
$('usbSave').addEventListener('click', () => {
  if (usbData) download(new Blob([usbData.slice()]), `fc-flash-${new Date().toISOString().slice(0, 10)}.bbl`);
});
$('usbClose').addEventListener('click', () => usbDialog.close());

// ---------- deep links: ?demo[&t=seconds] ----------
{
  const params = new URLSearchParams(location.search);
  if (params.has('demo')) {
    setFlight(simulateFlight());
    const t = Number(params.get('t'));
    if (Number.isFinite(t) && t > 0) seek(t);
  }
}
