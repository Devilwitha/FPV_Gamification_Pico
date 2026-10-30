import './styles.css';
import { indexAt } from './analysis/filters.ts';
import { MIN_EXAMPLES, TrickClassifier } from './analysis/ml.ts';
import { attitudeAt } from './analysis/orientation.ts';
import type { LabelExample } from './analysis/learned.ts';
import {
  DEFAULT_SETTINGS, TRICK_COLOR, TRICK_LABEL, TRICK_TYPES,
  detectTricks, getCustomTricks, registerCustomTricks, spanFeatures, summarize,
  type Analysis, type CustomTrick, type DetectSettings,
} from './analysis/tricks.ts';
import { MspClient, serialSupported } from './decoder/msp.ts';
import { simulateFlight } from './demo/simulate.ts';
import { audioEnvelope, correlate, estimateDrift, logSignal, throttlePunches } from './sync/audio.ts';
import type { FlightData, Trick, TrickType, WorkerRequest, WorkerResponse } from './types.ts';
import { DEFAULT_HUD, type HudOptions } from './ui/hud.ts';
import {
  SubmitRejectedError, checkDuplicate, deleteEntry, fetchAuth, fingerprints, fetchEntry, fetchLeaderboard, reportEntry, submitScore, verifyEntry,
  type AuthState, type EntryDetail, type SubmitMeta,
} from './ui/leaderboard.ts';
import { Timeline } from './ui/timeline.ts';
import { LogDecoder, largestLog } from './core/decoder.ts';
import { FlightPlayer } from './core/player.ts';
import { matchScore, type MatchScore } from './core/match.ts';
import { AUDIO_TYPES, AudioMixer, decodeMusic, sanitizeAudio, usesMixer, type OriginalMode, type RecordingAudio } from './core/audiomix.ts';
import { deleteOwnMark, fetchCatalog, fetchOwnMarks, submitMark, type OwnMark, type TrickCatalog } from './ui/tricklabels.ts';

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
// Flight, analysis, video sync and playback live in the shared FlightPlayer (core/player.ts).
const state = {
  /** applied sync anchors (video time ↔ log time); two of them also fix the drift */
  anchors: [] as { v: number; l: number }[],
  markVideo: null as number | null,
  markLog: null as number | null,
  activeTrickId: -1,
  showRejected: false,
  /** original log file and the log inside it – uploaded with a leaderboard submission */
  logFile: null as Blob | null,
  logName: '',
  logIndex: 0,
};
/** A span the pilot marked as trick ("not detected"). Gives no points until approved. */
interface Mark {
  id: number;
  start: number;
  end: number;
  /** trick key; empty while a new trick request is being sent */
  type: string;
  name: string;
  features: number[];
  status: 'local' | 'sending' | 'sent' | 'error';
  error?: string;
}
let markSeq = 0;
const marking = {
  marks: [] as Mark[],
  range: null as [number, number] | null,
  /** stop playback here (▶ Bereich abspielen) */
  playUntil: null as number | null,
};
let catalog: TrickCatalog = { online: false, loggedIn: false, custom: [], mine: [], examples: [] };
/** VS duel from ?match=<id> (see loadDuel) */
interface MatchInfo {
  id: number;
  limit: number;
  status: string;
  a: { username: string } | null;
  b: { username: string } | null;
  canUploadFor: string[];
  pageUrl: string;
  watchUrl: string;
  tournament: { name: string } | null;
}
const matchId = Number(new URLSearchParams(location.search).get('match')) || 0;
let duel: MatchInfo | null = null;
let duelScore: MatchScore | null = null;

let communityEnabled = (() => {
  try {
    return localStorage.getItem('bba.community') !== 'off';
  } catch {
    return true;
  }
})();
const trickLabel = (t: string) => TRICK_LABEL[t] ?? t;
/** Translation for attributes the DOM translator does not see (optgroup labels), see i18n-dom.js. */
function tr(s: string): string {
  try {
    return (window as unknown as { I18N?: { t: (s: string) => string } }).I18N?.t(s) ?? s;
  } catch {
    return s; // older i18n-dom.js without catalog (German)
  }
}
const trickColor = (t: string) => TRICK_COLOR[t] ?? '#f15bb5';

/** log index to open next (admin: re-open a submitted log exactly as submitted) */
let pendingLogIndex: number | null = null;
const ml = new TrickClassifier();
const video = $<HTMLVideoElement>('video');
const stage = $<HTMLCanvasElement>('stageCanvas');
stage.width = 1280;
stage.height = 720;
const player = new FlightPlayer(stage, video, {
  settings: load<DetectSettings>('bba.settings', DEFAULT_SETTINGS),
  hud: load<HudOptions>('bba.hud', DEFAULT_HUD),
});
// Ton & Musik der Aufnahmen (Reiter "Ton & Musik")
const mixer = new AudioMixer(video, sanitizeAudio(load<unknown>('bba.audio', null)));

const timeline = new Timeline($<HTMLCanvasElement>('timeline'), {
  onSeek: (t) => seek(t),
  onPick: (t) => {
    state.markLog = t;
    timeline.picking = false;
    timeline.setMarker(t);
    $('btnPickLog').textContent = 'In Timeline wählen';
    updateAnchorUi();
  },
  onRange: (t0, t1) => setMarkRange(t0, t1),
});

// ---------- decoding (core/decoder.ts) ----------
const decoder = new LogDecoder();
decoder.onProgress = (label, value) => busy(label, value);

/** Decode one log of the opened file and show it. */
async function openLog(index: number) {
  busy('Dekodiere…', 0);
  try {
    setFlight(await decoder.decode(index));
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
  } finally {
    busy(null);
  }
}

function busy(label: string | null, value = 0) {
  $('busy').hidden = label === null;
  if (label !== null) {
    $('busyLabel').textContent = label;
    $('busyBar').style.width = `${Math.round(value * 100)}%`;
  }
}

async function loadLogFile(file: File | Blob, name: string) {
  busy('Lese Datei…', 0.02);
  state.logFile = file;
  state.logName = name;
  let logs;
  try {
    ({ logs } = await decoder.open(file, name));
  } catch (err) {
    busy(null);
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
    return;
  }
  const sel = $<HTMLSelectElement>('logSelect');
  sel.replaceChildren(...logs.map((l) => el('option', { value: String(l.index), textContent: `Log ${l.index + 1} (${(l.bytes / 1024).toFixed(0)} KB)` })));
  $('logSelectRow').hidden = logs.length < 2;
  // default: the largest log (usually the actual flight)
  const best = largestLog(logs);
  const pick = pendingLogIndex !== null && logs.some((l) => l.index === pendingLogIndex) ? pendingLogIndex : best;
  if (logs.length > 1 && pendingLogIndex === null) toast(`${logs.length} Logs in der Datei gefunden – zeige Log ${best + 1}. Auswahl unter „Log-Info“.`);
  pendingLogIndex = null;
  sel.value = String(pick);
  state.logIndex = pick;
  await openLog(pick);
}

// ---------- flight / analysis ----------
function setFlight(f: FlightData) {
  player.setFlight(f);
  $('btnPlay').textContent = '▶';
  state.markLog = null;
  timeline.setMarker(null);
  marking.marks = [];
  marking.playUntil = null;
  setMarkRange(null);
  $('emptyState').hidden = true;
  for (const id of ['btnPlay', 'btnBack', 'btnFwd', 'btnRec', 'btnExport', 'btnExportVideo', 'btnMark']) $<HTMLButtonElement>(id).disabled = false;
  cellsSelect.value = f.info.cells ? String(f.info.cells) : '';
  $('cellsHint').textContent =
    f.info.source === 'demo' ? ''
    : f.info.vbat && f.info.cells ? `Akku erkannt: ${f.info.vbat.toFixed(1)} V beim Start → ${f.info.cells}S (bei Bedarf korrigieren).`
    : 'Akku-Spannung nicht im Log – bitte die Akku-Klasse wählen.';
  updateMarkUi();
  $('sourceChip').textContent = f.info.source === 'demo' ? 'Demo' : f.info.fileName;
  reanalyze(true);
  renderInfo();
  updateVideoUi();
  updateAnchorUi();
  updateSubmit();
  if (player.videoUrl) seek(Math.max(0, -player.offset / player.speed));
  else seek(0);
}

function reanalyze(resetView = false) {
  const f = player.flight;
  if (!f) return;
  player.analyze({
    labeler: ml.ready ? (feat) => ml.predict(feat) : null,
    learned: learnedExamples(),
  });
  if (!player.analysis) return;
  timeline.setData(f, player.analysis, resetView);
  timeline.setMarks(marking.marks.map((m) => ({ start: m.start, end: m.end, label: m.name, color: m.type ? trickColor(m.type) : '#f15bb5' })));
  renderTricks();
  renderScore();
  renderMl();
  updateSubmit();
}

function renderScore() {
  const a = player.analysis;
  const s = summarize(a?.tricks ?? []);
  $('scoreTotal').textContent = s.total.toLocaleString('de-DE');
  $('statTricks').textContent = String(s.count);
  $('statCombo').textContent = s.maxCombo ? `${s.maxCombo}×` : '0';
  $('statBest').textContent = s.best ? s.best.name : '–';
  $('statBest').title = s.best ? `${s.best.name}: ${s.best.points} Punkte` : '';
}

/** Examples for the community search: approved ones from the server plus (with local ML on) own corrections. */
function learnedExamples(): LabelExample[] {
  const list: LabelExample[] = communityEnabled ? [...catalog.examples] : [];
  if (ml.enabled) list.push(...ml.examples.map((e) => ({ label: e.label, f: e.f })));
  return list;
}

/** Options for a trick dropdown: built-in, community tricks and own requests. */
function trickOptions(selected: string, opts: { none?: boolean; placeholder?: string; request?: boolean } = {}): HTMLElement[] {
  const opt = (value: string, label: string) => el('option', { value, textContent: label, selected: value === selected });
  const out: HTMLElement[] = [];
  if (opts.placeholder) out.push(el('option', { value: '', textContent: opts.placeholder, selected: !selected }));
  const builtin = el('optgroup', { label: tr('Tricks') }, ...TRICK_TYPES.filter((t) => t !== 'none').map((t) => opt(t, TRICK_LABEL[t])));
  out.push(builtin);
  const approved = getCustomTricks().filter((c) => !c.pending);
  if (approved.length) out.push(el('optgroup', { label: tr('Community-Tricks') }, ...approved.map((c) => opt(c.key, c.name))));
  const pending = getCustomTricks().filter((c) => c.pending);
  if (pending.length) out.push(el('optgroup', { label: tr('Meine Anträge (noch nicht freigegeben)') }, ...pending.map((c) => opt(c.key, `${c.name} (beantragt)`))));
  if (opts.none) out.push(opt('none', TRICK_LABEL.none));
  if (opts.request) out.push(opt('__new', '＋ Neuen Trick beantragen …'));
  return out;
}

function trickRow(tr: Trick, rejected: boolean) {
  const select = el('select', { title: 'Label korrigieren (wird als Trainingsbeispiel gespeichert)' });
  select.setAttribute('aria-label', 'Trick-Label korrigieren');
  select.append(...trickOptions(tr.type, { none: true }));
  select.addEventListener('click', (e) => e.stopPropagation());
  select.addEventListener('change', () => {
    const label = select.value as TrickType;
    player.userLabels.set(tr.key, label);
    ml.add(tr.features, label);
    reanalyze();
    toast(`Als „${trickLabel(label)}“ markiert – ${ml.examples.length} Trainingsbeispiele.`);
  });
  const badges: Node[] = [];
  if (tr.source === 'ml') badges.push(el('span', { className: 'badge ml', textContent: 'ML' }));
  if (tr.source === 'user') badges.push(el('span', { className: 'badge user', textContent: 'manuell' }));
  if (tr.source === 'community') badges.push(el('span', { className: 'badge community', title: 'Vom Community-Modell erkannt', textContent: 'Community' }));
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
      ...(tr.repeat > 0 && !rejected ? [el('small', { className: 'repeat', title: 'Gleicher Trick direkt hintereinander gibt weniger Punkte', textContent: `WIEDERHOLUNG ×${tr.repeatMult.toFixed(2)}` })] : []),
    ),
  );
  (li.firstChild as HTMLElement).style.background = trickColor(tr.type);
  li.dataset.id = rejected ? '' : String(tr.id);
  li.addEventListener('click', () => seek(Math.max(0, tr.start - 0.8)));
  return li;
}

const MARK_STATUS: Record<Mark['status'], string> = {
  local: 'nur lokal',
  sending: 'wird gesendet …',
  sent: 'gesendet – wartet auf Freigabe',
  error: 'Senden fehlgeschlagen',
};

function markRow(m: Mark) {
  const remove = el('button', { className: 'icon-btn mark-remove', type: 'button', title: 'Markierung entfernen', textContent: '×' });
  remove.setAttribute('aria-label', 'Markierung entfernen');
  remove.addEventListener('click', (e) => {
    e.stopPropagation();
    marking.marks = marking.marks.filter((x) => x !== m);
    reanalyze();
  });
  const li = el(
    'li',
    { className: 'marked', title: m.error ?? '' },
    el('span', { className: 'swatch' }),
    el(
      'div',
      {},
      el('div', { className: 'name' }, m.name, el('span', { className: 'badge mark', textContent: 'markiert' })),
      el('div', { className: 'meta' }, `${fmtTime(m.start)} · ${(m.end - m.start).toFixed(2)} s · `, el('span', { textContent: MARK_STATUS[m.status] })),
    ),
    el('div', { className: 'pts' }, '–', remove),
  );
  (li.firstChild as HTMLElement).style.background = m.type ? trickColor(m.type) : '#f15bb5';
  li.addEventListener('click', () => seek(Math.max(0, m.start - 0.8)));
  return li;
}

function renderTricks() {
  const list = $('trickList');
  const a = player.analysis;
  if (!a) return list.replaceChildren();
  const rows = [
    ...a.tricks.map((t) => ({ start: t.start, node: () => trickRow(t, false) })),
    ...(state.showRejected ? a.rejected.map((t) => ({ start: t.start, node: () => trickRow(t, true) })) : []),
    ...marking.marks.map((m) => ({ start: m.start, node: () => markRow(m) })),
  ];
  rows.sort((x, y) => x.start - y.start);
  list.replaceChildren(...rows.map((r) => r.node()));
  if (!rows.length) list.append(el('li', { className: 'empty-list', textContent: 'Keine Tricks erkannt. Schwellwerte unter „Erkennung“ anpassen oder mit „✎ Trick markieren“ einen Bereich markieren.' }));
  state.activeTrickId = -1;
}

function highlightActive(t: number) {
  const a = player.analysis;
  if (!a) return;
  const cur = a.tricks.find((tr) => t >= tr.start - 0.05 && t <= tr.end + 1.5);
  const id = cur ? cur.id : -1;
  if (id === state.activeTrickId) return;
  state.activeTrickId = id;
  for (const li of $('trickList').children) {
    const on = (li as HTMLElement).dataset.id === String(id);
    li.classList.toggle('active', on);
    if (on && player.playing) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

// ---------- playback (core/player.ts) ----------
function hasVideo() {
  return player.hasVideo();
}
function duration() {
  return player.duration;
}
/** Video time ↔ log time, with clock drift: videoTime = offset + speed · logTime. */
function toVideoTime(t: number) {
  return player.toVideoTime(t);
}
function toLogTime(v: number) {
  return player.toLogTime(v);
}
function logTime() {
  return player.logTime();
}
function seek(t: number) {
  player.seek(t);
  mixer.restart(player.playing, player.rate);
}
function setPlaying(on: boolean) {
  player.setPlaying(on);
}
player.onPlayingChange = (on) => {
  $('btnPlay').textContent = on ? '❚❚' : '▶';
  if (on) mixer.onPlay(player.rate);
  else mixer.onPause();
};
player.onEnded = () => {
  if (recorder) stopRecording();
  subRec?.finish();
};

let lastNow = performance.now();
let lastLabel = '';
function frame(now: number) {
  requestAnimationFrame(frame);
  const dt = (now - lastNow) / 1000;
  lastNow = now;
  if (!player.flight) {
    player.render();
    return;
  }
  // without video the player's clock runs; at the end an ongoing recording stops
  if (player.advance(dt) && recorder) stopRecording();
  const t = logTime();
  subRec?.tick(t);
  if (marking.playUntil !== null && t >= marking.playUntil) {
    marking.playUntil = null;
    setPlaying(false);
  }
  player.render(t);
  timeline.setCursor(t);
  const label = `${fmtTime(t)} / ${fmtTime(duration())}`;
  if (label !== lastLabel) {
    $('timeLabel').textContent = label;
    lastLabel = label;
  }
  highlightActive(t);
}
requestAnimationFrame(frame);

$('btnPlay').addEventListener('click', () => setPlaying(!player.playing));
$('btnBack').addEventListener('click', () => seek(logTime() - 2));
$('btnFwd').addEventListener('click', () => seek(logTime() + 2));
$<HTMLSelectElement>('speed').addEventListener('change', (e) => {
  player.setRate(Number((e.target as HTMLSelectElement).value));
  mixer.restart(player.playing, player.rate);
});
document.addEventListener('keydown', (e) => {
  const tag = (e.target as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || !player.flight) return;
  if (e.code === 'Space') {
    e.preventDefault();
    setPlaying(!player.playing);
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
    const cb = el('input', { type: 'checkbox', checked: player.hudOptions[k] });
    cb.addEventListener('change', () => {
      player.hudOptions[k] = cb.checked;
      store('bba.hud', player.hudOptions);
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
      state.logFile = null;
      state.logName = '';
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
  state.logIndex = Number((e.target as HTMLSelectElement).value);
  void openLog(state.logIndex);
});

// ---------- video & sync ----------
function loadVideo(file: File) {
  player.loadVideo(file).then(
    () => {
      updateVideoUi();
      updateMarkUi();
      seek(logTime());
      toast(`Video geladen: ${file.name}`);
    },
    (err) => toast(err instanceof Error ? err.message : String(err), 'error'),
  );
}

function setOffset(v: number, speed = player.speed) {
  const t = logTime();
  player.setSync(v, speed);
  $<HTMLInputElement>('offset').value = player.offset.toFixed(3);
  $<HTMLInputElement>('drift').value = ((player.speed - 1) * 100).toFixed(3);
  updateVideoUi();
  updateMarkUi();
  if (player.flight && hasVideo() && !player.playing) seek(t);
}

function updateVideoUi() {
  const has = !!player.videoFile;
  $('videoStatus').textContent = has
    ? `Video: ${(player.videoFile as File).name || "Video"}${Number.isFinite(video.duration) && video.duration ? ` (${fmtTime(video.duration)})` : ''} – Log ${player.offset >= 0 ? 'startet' : 'beginnt'} bei Videozeit ${player.offset.toFixed(2)} s.` +
      (player.speed !== 1 ? ` Drift ${((player.speed - 1) * 100).toFixed(2)} % (Video läuft ${player.speed > 1 ? 'langsamer' : 'schneller'} als der Log).` : '')
    : 'Kein Video geladen. Ohne Video zeigt die Bühne eine synthetische FPV-Ansicht aus den Lagedaten.';
  $<HTMLButtonElement>('btnAutoSync').disabled = !(has && player.flight);
  $<HTMLButtonElement>('btnMarkVideo').disabled = !has;
  timeline.setVideoSpan(has && video.duration ? [toLogTime(0), toLogTime(video.duration)] : null);
}

$<HTMLInputElement>('offset').addEventListener('change', (e) => setOffset(Number((e.target as HTMLInputElement).value) || 0));
$<HTMLInputElement>('drift').addEventListener('change', (e) => setOffset(player.offset, 1 + (Number((e.target as HTMLInputElement).value) || 0) / 100));
$('btnDriftReset').addEventListener('click', () => setOffset(player.offset, 1));
document.querySelectorAll<HTMLButtonElement>('[data-nudge]').forEach((b) =>
  b.addEventListener('click', () => setOffset(player.offset + Number(b.dataset.nudge))),
);

$('btnAutoSync').addEventListener('click', async () => {
  const f = player.flight;
  if (!f || !player.videoFile) return;
  const btn = $<HTMLButtonElement>('btnAutoSync');
  btn.disabled = true;
  const out = $('syncResult');
  try {
    if (player.videoFile.size > 1.5e9) throw new Error('Video > 1,5 GB – bitte Anker-Methode verwenden.');
    const env = await audioEnvelope(player.videoFile, (m) => (out.textContent = m));
    const sig = logSignal(f);
    const res = correlate(env, sig);
    // Drift: Versatz am Anfang und am Ende des Flugs getrennt messen
    const drift = res.confidence >= 5 ? estimateDrift(env, sig, res.offset) : null;
    setOffset(drift ? drift.offset : res.offset, drift ? drift.speed : 1);
    state.anchors = [];
    updateAnchorUi();
    const sure = res.confidence >= 5;
    out.textContent = `Versatz ${player.offset.toFixed(2)} s · Konfidenz ${res.confidence.toFixed(1)}σ ${sure ? '✓' : '– unsicher, bitte prüfen / Anker verwenden'}` +
      (drift ? ` · Drift ${((drift.speed - 1) * 100).toFixed(2)} %` : sure && f.info.duration >= 75 ? ' · Drift nicht messbar – bei Bedarf zweiten Anker setzen' : '');
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
  const f = player.flight;
  $('markVideo').textContent = state.markVideo === null ? '–' : `Video ${state.markVideo.toFixed(3)} s`;
  $('markLog').textContent = state.markLog === null ? '–' : `Log ${state.markLog.toFixed(3)} s`;
  $<HTMLButtonElement>('btnPickLog').disabled = !f;
  $<HTMLButtonElement>('btnSnap').disabled = !f;
  $<HTMLButtonElement>('btnBeep').disabled = !f?.events.some((e) => e.type === 'sync_beep');
  $<HTMLButtonElement>('btnApplyAnchor').disabled = state.markVideo === null || state.markLog === null;
  $('anchorList').textContent = state.anchors.map((a, i) => `Anker ${i + 1}: Log ${a.l.toFixed(2)} s ↔ Video ${a.v.toFixed(2)} s`).join(' · ');
  $('btnAnchorReset').hidden = !state.anchors.length;
}
$('btnAnchorReset').addEventListener('click', () => {
  state.anchors = [];
  setOffset(player.offset, 1);
  updateAnchorUi();
});
$('btnMarkVideo').addEventListener('click', () => {
  state.markVideo = video.currentTime;
  updateAnchorUi();
});
$('btnPickLog').addEventListener('click', () => {
  timeline.picking = !timeline.picking;
  $('btnPickLog').textContent = timeline.picking ? 'Klick in die Timeline…' : 'In Timeline wählen';
});
$('btnBeep').addEventListener('click', () => {
  const beep = player.flight?.events.find((e) => e.type === 'sync_beep');
  if (!beep) return;
  state.markLog = beep.time;
  timeline.setMarker(beep.time);
  updateAnchorUi();
});
$('btnSnap').addEventListener('click', () => {
  const f = player.flight;
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
  const anchor = { v: state.markVideo, l: state.markLog };
  // ein Anker in der Nähe (< 20 s) wird ersetzt, sonst kommt er dazu – höchstens zwei
  const others = state.anchors.filter((a) => Math.abs(a.l - anchor.l) >= 20);
  const pair = others.length ? [others[others.length - 1], anchor].sort((a, b) => a.l - b.l) : [anchor];
  if (pair.length === 2) {
    const speed = (pair[1].v - pair[0].v) / (pair[1].l - pair[0].l);
    if (!(speed > 0.95 && speed < 1.05)) {
      toast(`Die beiden Anker passen nicht zusammen (Drift ${((speed - 1) * 100).toFixed(1)} %). Bitte die Stellen prüfen.`, 'error', 7000);
      return;
    }
    state.anchors = pair;
    setOffset(pair[0].v - speed * pair[0].l, speed);
    toast(`Versatz ${player.offset.toFixed(3)} s, Drift ${((speed - 1) * 100).toFixed(2)} % aus zwei Ankern.`);
  } else {
    state.anchors = pair;
    setOffset(anchor.v - player.speed * anchor.l);
    toast(`Versatz gesetzt: ${player.offset.toFixed(3)} s – für die Drift einen zweiten Anker weiter hinten im Flug setzen.`);
  }
  state.markVideo = null;
  state.markLog = null;
  timeline.setMarker(null);
  updateAnchorUi();
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
  { key: 'plMinDuration', label: 'Power Loop: min. Dauer', min: 0.4, max: 3, step: 0.05, fmt: sec, desc: 'Kürzere Pitch-Rotationen gelten als Flip (gilt auch für den Matty Flip)' },
  { key: 'plMinThrottle', label: 'Power Loop: min. Gas', min: 0.1, max: 0.9, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, desc: 'Durchschnittsgas während des Loops' },
  { key: 'yawMinPeak', label: 'Yaw Spin: min. Rate', min: 200, max: 1500, step: 10, fmt: deg, desc: 'Trennt Spins von Orbits' },
  { key: 'seqGap', label: 'Split-S: max. Pause', min: 0.1, max: 1.5, step: 0.05, fmt: sec, desc: 'Zwischen halber Rolle und halbem Loop' },
  { key: 'comboWindow', label: 'Combo-Fenster', min: 0.3, max: 4, step: 0.1, fmt: sec, desc: 'Nächster Trick innerhalb → Combo-Multiplikator' },
  { key: 'repeatGap', label: 'Wiederholung: max. Pause', min: 1, max: 15, step: 0.5, fmt: sec, desc: 'Gleicher Trick direkt danach → halbe Punkte; der 3. gleiche in Folge beginnt eine neue Combo' },
  { key: 'hangMinTime', label: 'Inverted Hang: min. Dauer', min: 0.3, max: 3, step: 0.05, fmt: sec, desc: 'So lange kopfüber (nur mit Beschleunigungssensor im Log)' },
  { key: 'diveMinTime', label: 'Dive: min. Dauer', min: 0.3, max: 3, step: 0.05, fmt: sec, desc: 'So lange mit der Nase nach unten (nur mit Beschleunigungssensor)' },
  { key: 'diveMaxThrottle', label: 'Dive: max. Gas', min: 0.05, max: 0.6, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, desc: 'Durchschnittsgas im Sturzflug' },
  { key: 'orbitMinTime', label: 'Orbit: min. Dauer', min: 1, max: 8, step: 0.25, fmt: sec, desc: 'Langsame 360°-Drehung um ein Objekt' },
];
let reanalyzeTimer = 0;
function renderSettings() {
  $('settings').replaceChildren(
    ...SETTING_DEFS.map((d) => {
      const out = el('output', { textContent: d.fmt(player.settings[d.key]) });
      const input = el('input', { type: 'range', min: String(d.min), max: String(d.max), step: String(d.step), value: String(player.settings[d.key]) });
      input.setAttribute('aria-label', d.label);
      input.addEventListener('input', () => {
        player.settings[d.key] = Number(input.value);
        out.textContent = d.fmt(player.settings[d.key]);
        store('bba.settings', player.settings);
        clearTimeout(reanalyzeTimer);
        reanalyzeTimer = window.setTimeout(() => reanalyze(), 120);
      });
      return el('div', { className: 'setting' }, el('span', { textContent: d.label }), out, input, el('span', { className: 'desc', textContent: d.desc }));
    }),
  );
}
renderSettings();
$('btnResetSettings').addEventListener('click', () => {
  player.settings = { ...DEFAULT_SETTINGS };
  store('bba.settings', player.settings);
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
      const s = el('span', { textContent: `${trickLabel(k)}: ${v}` });
      s.style.borderLeft = `3px solid ${trickColor(k)}`;
      return s;
    }),
  );
}

function renderCommunity() {
  $<HTMLInputElement>('communityEnabled').checked = communityEnabled;
  const custom = getCustomTricks().filter((c) => !c.pending);
  $('communityStats').replaceChildren(
    ...(catalog.online
      ? [
          el('span', { textContent: `${catalog.examples.length} freigegebene Beispiele` }),
          el('span', { textContent: `${custom.length} Community-Tricks` }),
          ...custom.map((c) => {
            const s = el('span', { textContent: `${c.name} · ${c.points} P.`, title: c.description ?? '' });
            s.style.borderLeft = `3px solid ${trickColor(c.key)}`;
            return s;
          }),
        ]
      : [el('span', { textContent: 'Server nicht erreichbar – Markierungen bleiben lokal.' })]),
  );
}

async function loadCatalog() {
  catalog = await fetchCatalog();
  registerCustomTricks([...catalog.custom, ...catalog.mine]);
  renderMarkTypes();
  renderCommunity();
  updateMarkUi();
  if (player.flight) reanalyze();
}

let ownMarks: OwnMark[] = [];
const OWN_STATUS: Record<OwnMark['status'], string> = { pending: 'wartet auf Prüfung', approved: 'freigegeben', rejected: 'abgelehnt' };
async function refreshOwnMarks() {
  ownMarks = catalog.loggedIn ? await fetchOwnMarks() : [];
  const list = $('myMarks');
  if (!catalog.loggedIn) {
    list.replaceChildren(el('li', { className: 'muted small', textContent: catalog.online ? 'Einloggen, um Markierungen zu senden und hier zu sehen.' : 'Server nicht erreichbar.' }));
    return;
  }
  if (!ownMarks.length) {
    list.replaceChildren(el('li', { className: 'muted small', textContent: 'Noch keine Markierungen gesendet.' }));
    return;
  }
  list.replaceChildren(
    ...ownMarks.map((m) => {
      const li = el(
        'li',
        { className: `own-${m.status}` },
        el('strong', { textContent: m.name }),
        el('span', { className: `badge status-${m.status}`, textContent: OWN_STATUS[m.status] ?? m.status }),
        el('span', { className: 'muted small', textContent: `${m.logName || 'Log'} · ${fmtTime(m.start)}–${fmtTime(m.end)} · ${new Date(m.createdAt).toLocaleDateString('de-DE')}` }),
      );
      if (m.note) li.append(el('span', { className: 'small', textContent: `Admin: ${m.note}` }));
      if (m.status === 'pending') {
        const del = el('button', { className: 'btn tiny', type: 'button', textContent: 'Zurückziehen' });
        del.addEventListener('click', async () => {
          if (!confirm('Markierung zurückziehen?')) return;
          try {
            await deleteOwnMark(m.id);
            toast('Markierung zurückgezogen.');
            await loadCatalog();
            void refreshOwnMarks();
          } catch (err) {
            toast((err as Error).message, 'error');
          }
        });
        li.append(del);
      }
      return li;
    }),
  );
}
$<HTMLInputElement>('communityEnabled').addEventListener('change', (e) => {
  communityEnabled = (e.target as HTMLInputElement).checked;
  try {
    localStorage.setItem('bba.community', communityEnabled ? 'on' : 'off');
  } catch {
    /* ignore */
  }
  reanalyze();
});
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
    if (btn.dataset.tab === 'ml') void refreshOwnMarks();
    if (btn.dataset.tab === 'audio') void loadMusicLibrary();
  }),
);
// Betaflight-Hinweis: vom Startbildschirm (und per #betaflight) direkt in den Tab springen
function openSetup() {
  $('tabSetup').click();
  $('betaflight').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
$('linkSetup').addEventListener('click', (e) => {
  e.preventDefault();
  openSetup();
});
if (location.hash === '#betaflight') openSetup();
$('btnCopyCli').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('setupCli').textContent ?? '');
    toast('CLI-Befehle kopiert – im Betaflight Configurator im Tab „CLI“ einfügen.');
  } catch {
    toast('Kopieren nicht möglich – bitte den Text markieren und kopieren.', 'error');
  }
});

// ---------- info ----------
function renderInfo() {
  const f = player.flight;
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
    ['Akku', i.vbat ? `${i.vbat.toFixed(2)} V beim Start → ${i.cells ? `${i.cells}S` : 'Klasse unklar'}` : 'Spannung nicht im Log'],
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
    basePoints: t.basePoints, comboMultiplier: t.comboMult, repeatMultiplier: t.repeatMult, points: t.points, source: t.source,
  };
}
exportMenu.addEventListener('click', (e) => {
  const kind = (e.target as HTMLElement).dataset.export;
  const f = player.flight;
  if (!kind || !f) return;
  const base = f.info.fileName.replace(/\.[^.]+$/, '');
  const tricks = player.analysis?.tricks ?? [];
  if (kind === 'csv') {
    if (f.info.source === 'demo') return toast('Der Demo-Flug hat keine Rohdaten – JSON-Export verwenden.');
    busy('Exportiere CSV…', 0);
    decoder.csv().then(({ blob, name }) => download(blob, name), (err) => toast(err instanceof Error ? err.message : String(err), 'error')).finally(() => busy(null));
  } else if (kind === 'tricks') {
    const data = { file: f.info.fileName, score: summarize(tricks).total, settings: player.settings, tricks: tricks.map(trickJson) };
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
/** Ton der Aufnahme: Mischung aus Musik und Originalton (core/audiomix.ts) oder wie bisher direkt der Videoton. */
function recordingAudio(stream: MediaStream, length: number | null, separate: boolean, plain = false): RecordingAudio | null {
  // Hängt das Video schon am AudioContext, läuft sein Ton nur noch über die Mischung.
  if (mixer.active || (!plain && usesMixer(mixer.settings, mixer.hasMusic))) {
    const mix = mixer.beginRecording(length, separate, plain);
    if (mix.track) stream.addTrack(mix.track);
    return mix;
  }
  if (hasVideo()) {
    const v = video as HTMLVideoElement & { captureStream?: () => MediaStream; mozCaptureStream?: () => MediaStream };
    const vs = v.captureStream?.() ?? v.mozCaptureStream?.();
    vs?.getAudioTracks().forEach((tr) => stream.addTrack(tr));
  }
  return null;
}

/** Originalton als eigene Tondatei mitschneiden ("separat speichern") */
interface AudioCapture { pause(): void; resume(): void; stop(): Promise<Blob | null> }
function captureAudio(stream: MediaStream): AudioCapture {
  const mime = AUDIO_TYPES.find((m) => MediaRecorder.isTypeSupported(m)) ?? '';
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start(1000);
  const blob = () => (chunks.length ? new Blob(chunks, { type: (rec.mimeType || 'audio/webm').split(';')[0] }) : null);
  return {
    pause: () => rec.state === 'recording' && rec.pause(),
    resume: () => rec.state === 'paused' && rec.resume(),
    stop: () => new Promise((resolve) => {
      if (rec.state === 'inactive') return resolve(blob());
      rec.onstop = () => resolve(blob());
      rec.stop();
    }),
  };
}
const audioExt = (b: Blob) => (b.type.includes('mp4') ? 'm4a' : b.type.includes('ogg') ? 'ogg' : 'webm');

let recorder: MediaRecorder | null = null;
let recChunks: Blob[] = [];
function startRecording() {
  if (!player.flight || typeof MediaRecorder === 'undefined') return toast('Aufnahme wird von diesem Browser nicht unterstützt.', 'error');
  const stream = stage.captureStream(30);
  const mix = recordingAudio(stream, null, true);
  const sep = mix?.separate ? captureAudio(mix.separate) : null;
  const mime = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find((m) => MediaRecorder.isTypeSupported(m)) ?? '';
  recorder = new MediaRecorder(stream, { mimeType: mime || undefined, videoBitsPerSecond: 8_000_000 });
  recChunks = [];
  recorder.ondataavailable = (e) => e.data.size && recChunks.push(e.data);
  recorder.onstop = async () => {
    const base = player.flight?.info.fileName.replace(/\.[^.]+$/, '') ?? 'flight';
    download(new Blob(recChunks, { type: 'video/webm' }), `${base}.hud.webm`);
    const original = sep ? await sep.stop() : null;
    mix?.stop();
    if (original) download(original, `${base}.originalton.${audioExt(original)}`);
    toast(original ? 'Aufnahme und Originalton gespeichert.' : 'Aufnahme gespeichert.');
  };
  recorder.start(1000);
  $('btnRec').classList.add('active');
  $('btnRec').textContent = '■ Stopp';
  if (!player.playing) setPlaying(true);
}
function stopRecording() {
  if (recorder?.state === 'recording') recorder.stop();
  recorder = null;
  $('btnRec').classList.remove('active');
  $('btnRec').textContent = '● Aufnehmen';
}
$('btnRec').addEventListener('click', () => (recorder ? stopRecording() : startRecording()));

// ---------- Ton & Musik (core/audiomix.ts) ----------
/** Hinweis im Aufnahme-Dialog: Musik und Originalton dieser Aufnahme */
function showAudioNote(upload: boolean) {
  const s = mixer.settings;
  const music = mixer.tracks.length === 1 ? `„${mixer.tracks[0].title}“` : mixer.tracks.length ? `${mixer.tracks.length} Songs` : '';
  const original: OriginalMode | null = hasVideo() && s.original !== 'keep' ? (upload ? 'remove' : s.original) : null;
  let text = '';
  if (music && original === 'remove') text = `Mit Musik ${music}, ohne Originalton.`;
  else if (music && original === 'separate') text = `Mit Musik ${music}, Originalton zusätzlich als eigene Datei.`;
  else if (music) text = `Mit Musik ${music}.`;
  else if (original === 'remove') text = 'Ohne Originalton.';
  else if (original === 'separate') text = 'Originalton zusätzlich als eigene Datei.';
  $('submitAudio').textContent = text;
  $('submitAudio').hidden = !text;
}

function saveAudio() {
  try {
    localStorage.setItem('bba.audio', JSON.stringify(mixer.settings));
  } catch {
    /* ignore */
  }
}
function fmtSong(sec: number) {
  return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
}
function renderPlaylist() {
  const list = $('playlist');
  list.replaceChildren(
    ...mixer.tracks.map((tr, i) => {
      const li = el('li', {});
      const btn = (text: string, label: string, disabled: boolean, act: () => void) => {
        const b = el('button', { type: 'button', textContent: text, title: label, disabled });
        b.setAttribute('aria-label', label);
        b.addEventListener('click', () => {
          act();
          audioChanged(true);
        });
        return b;
      };
      li.append(
        el('span', { className: 'pl-title', textContent: tr.title, title: tr.title }),
        el('span', { className: 'pl-dur', textContent: fmtSong(tr.buffer.duration) }),
        btn('↑', 'Nach oben', i === 0, () => mixer.moveTrack(i, -1)),
        btn('↓', 'Nach unten', i === mixer.tracks.length - 1, () => mixer.moveTrack(i, 1)),
        btn('✕', 'Aus der Playlist entfernen', false, () => mixer.removeTrack(i)),
      );
      return li;
    }),
  );
}
function renderAudio() {
  const s = mixer.settings;
  $<HTMLInputElement>('musicVolume').value = String(Math.round(s.musicVolume * 100));
  $('musicVolumeOut').textContent = `${Math.round(s.musicVolume * 100)} %`;
  $<HTMLInputElement>('origVolume').value = String(Math.round(s.originalVolume * 100));
  $('origVolumeOut').textContent = `${Math.round(s.originalVolume * 100)} %`;
  $<HTMLInputElement>('musicOffset').value = String(s.musicOffset);
  $<HTMLInputElement>('musicLoop').checked = s.loop;
  $<HTMLInputElement>('musicFade').checked = s.fade;
  $<HTMLInputElement>('audioMonitor').checked = s.monitor;
  document.querySelectorAll<HTMLInputElement>('input[name="origAudio"]').forEach((r) => (r.checked = r.value === s.original));
  $('origVolumeWrap').hidden = s.original !== 'keep';
  renderPlaylist();
  const n = mixer.tracks.length;
  $('musicInfo').textContent = n === 0 ? 'Keine Musik gewählt.' : n === 1 ? `1 Song · ${fmtSong(mixer.musicDuration)}` : `${n} Songs · ${fmtSong(mixer.musicDuration)}`;
  $<HTMLButtonElement>('btnMusicClear').disabled = !n;
  // Hinweis neben "Aufnehmen", wenn die Aufnahmen vom Videoton abweichen
  const badge = $('audioBadge');
  const parts = [n ? `♪ ${mixer.musicLabel}` : '', s.original === 'remove' ? 'ohne Originalton' : s.original === 'separate' ? 'Originalton separat' : ''].filter(Boolean);
  badge.textContent = parts.join(' · ');
  badge.hidden = !parts.length;
}
/** restart: die Vorschau neu planen (Playlist, Startstelle, Wiederholen); sonst nur Lautstärken */
function audioChanged(restart = true) {
  saveAudio();
  renderAudio();
  mixer.refresh(player.playing, player.rate, restart);
}
$<HTMLInputElement>('musicFile').addEventListener('change', async (e) => {
  const input = e.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  input.value = '';
  let added = 0;
  for (const file of files) {
    $('musicInfo').textContent = `Lade „${file.name}“ …`;
    try {
      const buffer = await decodeMusic(await file.arrayBuffer(), file.name);
      mixer.addTrack({ title: file.name.replace(/\.[^.]+$/, ''), origin: 'file', buffer });
      added++;
    } catch (err) {
      toast((err as Error).message, 'error', 7000);
    }
  }
  if (added) toast(added === 1 ? 'Song zur Playlist hinzugefügt.' : `${added} Songs zur Playlist hinzugefügt.`);
  audioChanged();
});
$('btnMusicClear').addEventListener('click', () => {
  mixer.clearMusic();
  audioChanged();
});
$<HTMLInputElement>('musicVolume').addEventListener('input', (e) => {
  mixer.settings.musicVolume = Number((e.target as HTMLInputElement).value) / 100;
  audioChanged(false);
});
$<HTMLInputElement>('origVolume').addEventListener('input', (e) => {
  mixer.settings.originalVolume = Number((e.target as HTMLInputElement).value) / 100;
  audioChanged(false);
});
$<HTMLInputElement>('musicOffset').addEventListener('change', (e) => {
  const v = Number((e.target as HTMLInputElement).value);
  mixer.settings.musicOffset = Number.isFinite(v) ? Math.max(0, Math.min(3600, v)) : 0;
  audioChanged();
});
$<HTMLInputElement>('musicLoop').addEventListener('change', (e) => {
  mixer.settings.loop = (e.target as HTMLInputElement).checked;
  audioChanged();
});
$<HTMLInputElement>('musicFade').addEventListener('change', (e) => {
  mixer.settings.fade = (e.target as HTMLInputElement).checked;
  audioChanged(false);
});
$<HTMLInputElement>('audioMonitor').addEventListener('change', (e) => {
  mixer.settings.monitor = (e.target as HTMLInputElement).checked;
  audioChanged();
});
document.querySelectorAll<HTMLInputElement>('input[name="origAudio"]').forEach((r) =>
  r.addEventListener('change', () => {
    if (r.checked) mixer.settings.original = r.value as OriginalMode;
    audioChanged(false);
  }),
);
$('audioBadge').addEventListener('click', () => $('tabAudio').click());
$('submitDialog').addEventListener('close', () => ($('submitAudio').hidden = true));
renderAudio();

// Musik vom Server (webshop/musik.py: Ordner <Repo>/musik)
interface LibraryTrack { id: string; title: string; group: string; size: number }
let library: LibraryTrack[] | null = null;
const libAudio = new Audio();
let libPlaying = '';
const libUrl = (id: string) => `../api/musik/datei/${id.split('/').map(encodeURIComponent).join('/')}`;
function stopLibPreview() {
  libAudio.pause();
  libPlaying = '';
  renderLibrary();
}
libAudio.addEventListener('ended', stopLibPreview);
function renderLibrary() {
  const list = $('musicLib');
  if (!library) return;
  const q = $<HTMLInputElement>('libFilter').value.trim().toLowerCase();
  const shown = library.filter((t) => !q || `${t.group} ${t.title}`.toLowerCase().includes(q));
  list.replaceChildren(
    ...shown.map((t) => {
      const li = el('li', {});
      li.classList.toggle('playing', libPlaying === t.id);
      const play = el('button', { type: 'button', className: 'lib-play', textContent: libPlaying === t.id ? '■' : '▶', title: 'Reinhören' });
      play.setAttribute('aria-label', 'Reinhören');
      play.addEventListener('click', () => {
        if (libPlaying === t.id) return stopLibPreview();
        libAudio.src = libUrl(t.id);
        libPlaying = t.id;
        void libAudio.play().catch(() => stopLibPreview());
        renderLibrary();
      });
      const title = el('span', { className: 'lib-title', textContent: t.title, title: t.title });
      if (t.group) title.append(el('small', { textContent: t.group }));
      const add = el('button', { type: 'button', className: 'btn tiny', textContent: '+ Hinzufügen' });
      add.addEventListener('click', async () => {
        add.disabled = true;
        add.textContent = 'Lädt …';
        try {
          const res = await fetch(libUrl(t.id));
          if (!res.ok) throw new Error(`„${t.title}“ konnte nicht geladen werden (HTTP ${res.status}).`);
          const buffer = await decodeMusic(await res.arrayBuffer(), t.title);
          mixer.addTrack({ title: t.title, origin: 'server', serverId: t.id, buffer });
          toast(`„${t.title}“ zur Playlist hinzugefügt.`);
          audioChanged();
        } catch (err) {
          toast((err as Error).message, 'error', 7000);
        }
        add.disabled = false;
        add.textContent = '+ Hinzufügen';
      });
      li.append(play, title, add);
      return li;
    }),
  );
}
async function loadMusicLibrary() {
  if (library) return;
  try {
    const res = await fetch('../api/musik', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error(String(res.status));
    library = ((await res.json()).tracks ?? []) as LibraryTrack[];
  } catch {
    $('libInfo').textContent = 'Die Musik-Liste des Servers ist gerade nicht erreichbar.';
    return;
  }
  $('libInfo').textContent = library.length ? `${library.length} Songs auf dem Server – mit ▶ reinhören, mit + zur Playlist.` : 'Auf dem Server liegt noch keine Musik.';
  $('libFilter').hidden = library.length < 8;
  renderLibrary();
}
$<HTMLInputElement>('libFilter').addEventListener('input', () => renderLibrary());

// ---------- leaderboard ----------
const pilotInput = $<HTMLInputElement>('pilotName');
const cellsSelect = $<HTMLSelectElement>('cellsSelect');
cellsSelect.addEventListener('change', () => updateSubmit());
pilotInput.value = localStorage.getItem('bba.pilot') ?? '';
// Login requirement for submitting (set by the server, see server/leaderboard.mjs).
let auth: AuthState = { required: false, loggedIn: true, loginUrl: '', isAdmin: false, username: null };
const loginBlocked = () => auth.required && !auth.loggedIn;
function showAuth() {
  $('loginHint').hidden = !loginBlocked();
  $<HTMLAnchorElement>('loginLink').href = auth.loginUrl || '/login';
  updateSubmit();
}
async function refreshAuth() {
  auth = await fetchAuth();
  if (auth.username && !pilotInput.value.trim()) pilotInput.value = auth.username;
  showAuth();
}
// Demo flights are synthetic and must not end up in the leaderboard.
const isDemoFlight = () => player.flight?.info.source === 'demo';
function updateSubmit() {
  updateMatchCard();
  $('demoHint').hidden = !isDemoFlight();
  $<HTMLButtonElement>('btnSubmit').disabled =
    loginBlocked() || isDemoFlight() || !player.flight || !pilotInput.value.trim() || !cellsSelect.value || !(player.analysis?.tricks.length);
  $<HTMLButtonElement>('btnSubmit').title = cellsSelect.value || !player.flight ? '' : 'Bitte zuerst die Akku-Klasse wählen.';
}
pilotInput.addEventListener('input', () => {
  try {
    localStorage.setItem('bba.pilot', pilotInput.value.trim());
  } catch {
    /* ignore */
  }
  updateSubmit();
});
// ---------- submit: record the flight with overlay, then upload ----------
// The leaderboard keeps a video of the flight with HUD overlay. It is recorded here by playing
// the flight once in real time (MediaRecorder on the stage canvas), then uploaded with the log.
interface SubRec { tick(t: number): void; finish(): void; cancel(): void }
let subRec: SubRec | null = null;

function waitSeeked(): Promise<void> {
  if (!hasVideo() || !video.seeking) return Promise.resolve();
  return new Promise((res) => video.addEventListener('seeked', () => res(), { once: true }));
}

interface RecordOptions {
  /** null = original video resolution (export), otherwise cap the height (leaderboard: 720) */
  maxHeight: number | null;
  bitrate: number;
  preferMp4: boolean;
  /** only this log span (trick marks), otherwise the whole flight */
  range?: [number, number];
  /** 'original' = Videoton unverändert, ohne Musik (Markierungen für den Admin) */
  audio?: 'mix' | 'original';
  /** Originalton bei "separat speichern" als eigene Datei zurückgeben (Export) */
  separate?: boolean;
}
interface Recorded { blob: Blob; videoStart: number; originalAudio: Blob | null }
const MP4_TYPES = ['video/mp4;codecs=avc1.640033,mp4a.40.2', 'video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4;codecs=avc1', 'video/mp4'];
const WEBM_TYPES = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'];

async function recordFlight(onProgress: (v: number) => void, opts: RecordOptions): Promise<Recorded> {
  if (typeof MediaRecorder === 'undefined' || !stage.captureStream) {
    throw new Error('Dieser Browser kann kein Video aufnehmen. Bitte Chrome, Edge oder Firefox verwenden.');
  }
  if (recorder) stopRecording();
  // a running "▶ Bereich abspielen" preview would pause the video mid-recording
  marking.playUntil = null;
  setPlaying(false);
  const prevRate = player.rate;
  player.setRate(1);
  seek(opts.range?.[0] ?? 0);
  await waitSeeked();
  const videoStart = Math.max(0, logTime());
  const stopAt = opts.range?.[1] ?? duration();
  // Recorded is always the stage (video + overlay), never the original file. Leaderboard: capped at
  // 720p / 2.5 Mbit/s to save space; export: the original video resolution (up to 4K).
  const prevSize = [stage.width, stage.height];
  if (opts.maxHeight === null) {
    const [w, h] = exportSize();
    stage.width = w;
    stage.height = h;
  } else if (stage.height > opts.maxHeight) {
    stage.width = Math.round((stage.width * opts.maxHeight) / stage.height / 2) * 2;
    stage.height = opts.maxHeight;
  }
  const stream = stage.captureStream(30);
  const mix = recordingAudio(stream, Math.max(0.5, stopAt - videoStart), !!opts.separate, opts.audio === 'original');
  const sep = mix?.separate ? captureAudio(mix.separate) : null;
  const mime = (opts.preferMp4 ? [...MP4_TYPES, ...WEBM_TYPES] : [...WEBM_TYPES, ...MP4_TYPES]).find((m) => MediaRecorder.isTypeSupported(m)) ?? '';
  const rec = new MediaRecorder(stream, { mimeType: mime || undefined, videoBitsPerSecond: opts.bitrate });
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  return new Promise((resolve, reject) => {
    let done = false;
    // In a background tab the browser stops drawing – pause instead of recording a frozen frame.
    const onVisibility = () => {
      if (done) return;
      if (document.hidden) {
        setPlaying(false);
        if (rec.state === 'recording') rec.pause();
        sep?.pause();
        mix?.pause();
      } else if (rec.state === 'paused') {
        mix?.resume();
        sep?.resume();
        rec.resume();
        setPlaying(true);
      }
    };
    const end = (err?: Error) => {
      if (done) return;
      done = true;
      subRec = null;
      document.removeEventListener('visibilitychange', onVisibility);
      setPlaying(false);
      player.setRate(prevRate);
      [stage.width, stage.height] = prevSize;
      rec.onstop = async () => {
        const originalAudio = sep ? await sep.stop() : null;
        mix?.stop();
        stream.getTracks().forEach((tr) => tr.stop());
        if (err) reject(err);
        else resolve({ blob: new Blob(chunks, { type: (rec.mimeType || 'video/webm').split(';')[0] }), videoStart, originalAudio });
      };
      if (rec.state !== 'inactive') rec.stop();
      else (rec.onstop as (ev: Event) => void).call(rec, new Event('stop'));
    };
    subRec = {
      tick: (t) => {
        onProgress(Math.min(1, Math.max(0, (t - videoStart) / Math.max(0.1, stopAt - videoStart))));
        if (t >= stopAt - 0.02) end();
      },
      finish: () => end(),
      cancel: () => end(new Error('Abgebrochen.')),
    };
    document.addEventListener('visibilitychange', onVisibility);
    rec.start(1000);
    setPlaying(true);
  });
}

const submitDialog = $<HTMLDialogElement>('submitDialog');
let submitting = false;
submitDialog.addEventListener('cancel', (e) => submitting && e.preventDefault());
$('submitClose').addEventListener('click', () => submitDialog.close());
function submitPhase(text: string, v: number) {
  $('submitPhase').textContent = text;
  $('submitBar').style.width = `${Math.round(v * 100)}%`;
}

// Export: original video resolution (max. 4K) with overlay, as MP4 where the browser can record it.
function exportSize(): [number, number] {
  if (hasVideo() && video.videoWidth) {
    const k = Math.min(1, 3840 / video.videoWidth, 2160 / video.videoHeight);
    return [Math.round((video.videoWidth * k) / 2) * 2, Math.round((video.videoHeight * k) / 2) * 2];
  }
  return [1920, 1080];
}
$('btnExportVideo').addEventListener('click', async () => {
  const f = player.flight;
  if (!f || submitting) return;
  const [w, h] = exportSize();
  const mp4 = typeof MediaRecorder !== 'undefined' && MP4_TYPES.some((m) => MediaRecorder.isTypeSupported(m));
  $('submitTitle').textContent = 'Video mit Overlay exportieren';
  $('submitText').textContent =
    `${hasVideo() ? `Dein Video wird in Originalauflösung (${w}×${h})` : `Die Sim-Ansicht wird in ${w}×${h}`} mit Overlay einmal abgespielt und ` +
    `als ${mp4 ? 'MP4' : 'WebM (dieser Browser kann kein MP4 aufnehmen)'} aufgenommen – das dauert so lange wie der Flug (${fmtTime(duration())}). ` +
    'Die Datei bleibt auf deinem PC, es wird nichts hochgeladen.';
  showAudioNote(false);
  $('submitCancel').hidden = false;
  $('submitView').hidden = true;
  $('submitClose').hidden = true;
  submitPhase('Aufnahme startet …', 0);
  submitDialog.showModal();
  submitting = true;
  $('submitCancel').onclick = () => subRec?.cancel();
  try {
    const bitrate = Math.round(Math.min(40e6, Math.max(6e6, w * h * 30 * 0.12)));
    const { blob, originalAudio } = await recordFlight((v) => submitPhase(`Aufnahme läuft … ${Math.round(v * 100)} %`, v), {
      maxHeight: null, bitrate, preferMp4: true, separate: true,
    });
    const ext = blob.type.includes('mp4') ? 'mp4' : 'webm';
    const base = f.info.fileName.replace(/\.[^.]+$/, '') || 'flug';
    download(blob, `${base}.overlay.${ext}`);
    if (originalAudio) download(originalAudio, `${base}.originalton.${audioExt(originalAudio)}`);
    submitPhase(`Gespeichert: ${(blob.size / 1048576).toFixed(1)} MB als ${ext.toUpperCase()} in ${w}×${h}.`, 1);
    if (originalAudio) $('submitAudio').textContent = `Originalton separat gespeichert (${audioExt(originalAudio).toUpperCase()}, ${(originalAudio.size / 1048576).toFixed(1)} MB).`;
  } catch (err) {
    submitPhase((err as Error).message || 'Aufnahme fehlgeschlagen.', 0);
  } finally {
    submitting = false;
    $('submitCancel').hidden = true;
    $('submitClose').hidden = false;
  }
});

$('submitForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = player.flight;
  const analysis = player.analysis;
  if (!f || !analysis || submitting) return;
  if (f.info.source === 'demo') return toast('Demo-Flüge können nicht eingereicht werden.', 'error');
  const s = summarize(analysis.tricks);
  $('submitText').textContent =
    `Dein Flug (${fmtTime(duration())}) wird jetzt einmal mit Overlay abgespielt und als Video aufgenommen – ` +
    'das dauert so lange wie der Flug. Lass diesen Tab geöffnet; im Hintergrund pausiert die Aufnahme.';
  showAudioNote(true);
  $('submitCancel').hidden = false;
  $('submitView').hidden = true;
  $('submitClose').hidden = true;
  $('submitTitle').textContent = 'Score einreichen';
  submitPhase('Prüfe, ob der Flug schon eingereicht wurde …', 0);
  submitDialog.showModal();
  submitting = true;
  const ctl = new AbortController();
  $('submitCancel').onclick = () => {
    ctl.abort();
    subRec?.cancel();
  };
  try {
    // each log / flight / video only once – checked before the long recording
    const prints = await fingerprints(f, state.logFile, state.logIndex, player.videoFile);
    const duplicate = await checkDuplicate(prints);
    if (duplicate) throw new SubmitRejectedError(duplicate, false, '');
    if (ctl.signal.aborted) throw new Error('Abgebrochen.');
    submitPhase('Aufnahme startet …', 0);
    const { blob, videoStart } = await recordFlight((v) => submitPhase(`Aufnahme läuft … ${Math.round(v * 100)} %`, v * 0.7), {
      maxHeight: 720, bitrate: 2_500_000, preferMp4: false,
    });
    if (ctl.signal.aborted) throw new Error('Abgebrochen.');
    const meta: SubmitMeta = {
      source: f.info.source,
      pilot: pilotInput.value.trim(),
      score: s.total,
      tricks: s.count,
      best: s.best?.name ?? '',
      maxCombo: s.maxCombo,
      craft: f.info.craft,
      cells: Number(cellsSelect.value) || null,
      duration: f.info.duration,
      logName: state.logName || f.info.fileName,
      logIndex: state.logIndex,
      trickList: analysis.tricks.map((t) => ({
        type: t.type, name: t.name, count: t.count, start: +t.start.toFixed(3), end: +t.end.toFixed(3),
        points: t.points, comboMultiplier: t.comboMult, source: t.source,
      })),
      settings: { ...player.settings },
      videoStart,
      flightHash: prints.flightHash,
      videoHash: prints.videoHash,
    };
    submitPhase(`Lade hoch (${(blob.size / 1048576).toFixed(1)} MB) …`, 0.7);
    const res = await submitScore(meta, blob, state.logFile, (v) => submitPhase(`Lade hoch … ${Math.round(v * 100)} %`, 0.7 + v * 0.3), ctl.signal);
    submitPhase(`Eingereicht – Platz ${res.rank}.`, 1);
    $('submitView').hidden = false;
    $('submitView').onclick = () => {
      submitDialog.close();
      void openEntry(res.id);
    };
    refreshBoard();
  } catch (err) {
    if (err instanceof SubmitRejectedError && err.loginRequired) {
      // Session expired or logged out in another tab.
      auth = { ...auth, required: true, loggedIn: false, loginUrl: err.loginUrl || auth.loginUrl };
      showAuth();
    }
    submitPhase((err as Error).message || 'Einreichen fehlgeschlagen.', 0);
  } finally {
    submitting = false;
    $('submitCancel').hidden = true;
    $('submitClose').hidden = false;
  }
});
// Re-check after returning to the tab, e.g. after logging in in another tab.
window.addEventListener('focus', () => void refreshAuth());
void refreshAuth();
async function refreshBoard() {
  const { entries, online } = await fetchLeaderboard(Number($<HTMLSelectElement>('boardCells').value) || null);
  $('boardMode').textContent = online ? 'Online-Leaderboard – Eintrag anklicken für Video und Tricks' : 'Lokales Leaderboard (Server nicht erreichbar)';
  $('boardFull').hidden = !online;
  const me = pilotInput.value.trim().toLowerCase();
  $('boardBody').replaceChildren(
    ...(entries.length
      ? entries.map((e, k) => {
          const pilot = el('td', {}, e.pilot);
          if (e.country) pilot.append(' ', el('span', { className: 'badge', title: 'Land', textContent: e.country }));
          if (e.hasVideo) pilot.append(' ', el('span', { className: 'badge', title: 'Video mit Overlay', textContent: '▶' }));
          if (e.verified) pilot.append(' ', el('span', { className: 'badge user', title: 'Vom Admin geprüft', textContent: '✓ geprüft' }));
          if (e.reportCount) pilot.append(' ', el('span', { className: 'badge report', title: 'Meldungen (nur Admin)', textContent: `⚑ ${e.reportCount}` }));
          const openable = online && typeof e.id === 'number';
          const row = el(
            'tr',
            { className: `${e.mine || e.pilot.toLowerCase() === me ? 'me' : ''}${openable ? ' clickable' : ''}` },
            el('td', { textContent: String(e.rank ?? k + 1) }),
            pilot,
            el('td', { textContent: e.score.toLocaleString('de-DE') }),
            el('td', { textContent: e.cells ? `${e.cells}S` : '–' }),
            el('td', { textContent: String(e.tricks) }),
            el('td', { textContent: e.best || '–' }),
            el('td', { textContent: e.maxCombo ? `${e.maxCombo}×` : '–' }),
            el('td', { textContent: e.createdAt ? new Date(e.createdAt).toLocaleDateString('de-DE') : '' }),
          );
          if (openable) {
            row.tabIndex = 0;
            row.title = 'Video und Tricks ansehen';
            row.addEventListener('click', () => void openEntry(e.id as number));
            row.addEventListener('keydown', (ev) => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), void openEntry(e.id as number)));
          }
          return row;
        })
      : [el('tr', {}, el('td', { colSpan: 8, className: 'muted', textContent: 'Noch keine Einträge.' }))]),
  );
}

// ---------- duel (VS): ?match=<id> – submit the flight's scoring window to a match ----------

async function loadDuel() {
  if (!matchId) return;
  try {
    const res = await fetch(`../api/vs/matches/${matchId}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error(res.status === 404 ? 'Duell nicht gefunden.' : `Duell konnte nicht geladen werden (HTTP ${res.status}).`);
    duel = (await res.json()).match as MatchInfo;
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
    return;
  }
  $('matchCard').hidden = false;
  $<HTMLAnchorElement>('matchPageLink').href = duel.pageUrl;
  const names = `${duel.a?.username ?? '?'} vs ${duel.b?.username ?? '?'}`;
  $('matchInfo').textContent = `${names}${duel.tournament ? ` · ${duel.tournament.name}` : ''} · Wertungszeit ${duel.limit >= 60 && duel.limit % 60 === 0 ? `${duel.limit / 60} min` : `${duel.limit} s`}`;
  const sel = $<HTMLSelectElement>('matchPilot');
  sel.replaceChildren(...duel.canUploadFor.map((u) => el('option', { value: u, textContent: u })));
  $('matchPilotWrap').hidden = duel.canUploadFor.length < 2;
  updateMatchCard();
}

function updateMatchCard() {
  if (!duel) return;
  const f = player.flight, a = player.analysis;
  duelScore = f && a ? matchScore(f, a, player.settings, duel.limit) : null;
  $('matchScore').textContent = duelScore ? duelScore.summary.total.toLocaleString('de-CH') : '–';
  $('matchDetail').textContent = duelScore
    ? `${duelScore.summary.count} Tricks in ${duel.limit} s ab dem Abheben (${fmtTime(duelScore.start)})`
    : 'Log laden, um die Duell-Wertung zu sehen.';
  const hint = $('matchHint');
  const problem =
    duel.status !== 'open' ? 'Dieses Duell ist schon entschieden.'
    : !duel.canUploadFor.length ? (auth.loggedIn ? 'Du kannst in diesem Duell keinen Flug (mehr) einreichen.' : 'Zum Einreichen bitte einloggen.')
    : isDemoFlight() ? 'Demo-Flüge zählen nicht – lade dein eigenes Blackbox-Log.'
    : '';
  hint.textContent = problem;
  hint.hidden = !problem;
  $<HTMLButtonElement>('btnMatchSubmit').disabled = !!problem || !duelScore || submitting;
}

function uploadForm(url: string, form: FormData, onProgress: (v: number) => void): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('X-Requested-With', 'blackbox-viewer');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status === 201) resolve(data);
      else reject(new Error(typeof data.error === 'string' ? data.error : xhr.status === 413 ? 'Zu groß für den Server.' : `Einreichen fehlgeschlagen (HTTP ${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error('Server nicht erreichbar – bitte später erneut versuchen.'));
    xhr.send(form);
  });
}

$('btnMatchSubmit').addEventListener('click', async () => {
  const f = player.flight, score = duelScore;
  if (!duel || !f || !score || submitting) return;
  const pilot = $<HTMLSelectElement>('matchPilot').value || duel.canUploadFor[0];
  $('submitTitle').textContent = 'Ins Duell einreichen';
  $('submitText').textContent =
    `Die Wertungszeit (${duel.limit} s ab dem Abheben) wird einmal mit Overlay abgespielt und als Clip aufgenommen – ` +
    'das dauert so lange wie die Wertungszeit. Lass diesen Tab geöffnet.';
  showAudioNote(true);
  $('submitCancel').hidden = false;
  $('submitView').hidden = true;
  $('submitClose').hidden = true;
  submitPhase('Aufnahme startet …', 0);
  submitDialog.showModal();
  submitting = true;
  updateMatchCard();
  $('submitCancel').onclick = () => subRec?.cancel();
  try {
    const prints = await fingerprints(f, state.logFile, state.logIndex, player.videoFile);
    const range: [number, number] = [Math.max(0, score.start - 1), Math.min(duration(), score.end + 1)];
    const { blob, videoStart } = await recordFlight((v) => submitPhase(`Aufnahme läuft … ${Math.round(v * 100)} %`, v * 0.7), {
      maxHeight: 720, bitrate: 2_500_000, preferMp4: false, range,
    });
    const meta = {
      pilot,
      source: f.info.source,
      score: score.summary.total,
      tricks: score.summary.count,
      best: score.summary.best?.name ?? '',
      maxCombo: score.summary.maxCombo,
      cells: Number(cellsSelect.value) || f.info.cells || null,
      craft: f.info.craft,
      windowStart: +score.start.toFixed(3),
      limit: duel.limit,
      logName: state.logName || f.info.fileName,
      logIndex: state.logIndex,
      videoStart,
      trickList: score.tricks.map((t) => ({
        type: t.type, name: t.name, count: t.count, start: +t.start.toFixed(3), end: +t.end.toFixed(3),
        points: t.points, comboMultiplier: t.comboMult, source: t.source,
      })),
      settings: { ...player.settings },
      flightHash: prints.flightHash,
      videoHash: prints.videoHash,
    };
    const form = new FormData();
    form.append('meta', JSON.stringify(meta));
    form.append('video', blob, blob.type.includes('mp4') ? 'duell.mp4' : 'duell.webm');
    if (state.logFile) form.append('log', state.logFile, state.logName || 'flug.bbl');
    submitPhase(`Lade hoch (${(blob.size / 1048576).toFixed(1)} MB) …`, 0.7);
    const data = await uploadForm(`../api/vs/matches/${duel.id}/flug`, form, (v) => submitPhase(`Lade hoch … ${Math.round(v * 100)} %`, 0.7 + v * 0.3));
    duel = (data.match as MatchInfo) ?? duel;
    submitPhase(
      duel.status === 'done' ? 'Eingereicht – das Duell ist entschieden!' : `Eingereicht${pilot !== auth.username ? ` – ${pilot} muss den Flug noch bestätigen` : ''}. Sobald beide Flüge da sind, steht der Sieger fest.`,
      1,
    );
    $('submitView').hidden = false;
    $('submitView').textContent = 'Zur Duell-Seite';
    $('submitView').onclick = () => (location.href = duel!.pageUrl);
  } catch (err) {
    submitPhase((err as Error).message || 'Einreichen fehlgeschlagen.', 0);
  } finally {
    submitting = false;
    $('submitCancel').hidden = true;
    $('submitClose').hidden = false;
    updateMatchCard();
  }
});
void refreshAuth().then(() => loadDuel());

// ---------- marking: span → trick (or new trick request), sent with a short clip ----------
const MARK_MIN = 0.1;
const MARK_MAX = 30;
const markType = $<HTMLSelectElement>('markType');
const markSend = $<HTMLInputElement>('markSend');

function renderMarkTypes() {
  const current = markType.value;
  markType.replaceChildren(...trickOptions(current, { placeholder: 'Trick wählen …', request: true }));
  if (![...markType.options].some((o) => o.value === current)) markType.value = '';
}

function setMarking(on: boolean) {
  timeline.marking = on;
  $('markPanel').hidden = !on;
  $('btnMark').classList.toggle('active', on);
  $('timelineHint').textContent = on
    ? 'Ziehen = Bereich markieren · Klick = springen · Mausrad = Zoom'
    : 'Klick = springen · Ziehen = verschieben · Mausrad = Zoom · Doppelklick = alles';
  if (!on) setMarkRange(null);
  updateMarkUi();
}

function setMarkRange(t0: number | null, t1?: number) {
  if (t0 === null || t1 === undefined || !player.flight) {
    marking.range = null;
  } else {
    const a = Math.max(0, Math.min(t0, t1)), b = Math.min(duration(), Math.max(t0, t1));
    marking.range = [a, b];
  }
  timeline.setRange(marking.range);
  updateMarkUi();
}

/** Angles etc. of the marked span (same features as the classifier uses). */
function rangeFeatures() {
  const f = player.flight, a = player.analysis, r = marking.range;
  if (!f || !a || !r) return null;
  const i0 = indexAt(f.t, r[0]), i1 = indexAt(f.t, r[1]);
  return i1 > i0 ? spanFeatures(f, a, i0, i1) : null;
}

/** The admin reviews the real flight video: sending needs a loaded video that covers the marked span. */
function videoCoversRange(): boolean {
  const r = marking.range;
  if (!hasVideo() || !r) return hasVideo();
  return toVideoTime(r[0]) >= -0.05 && toVideoTime(r[1]) <= (video.duration || 0) + 0.05;
}
const canSend = () => catalog.online && catalog.loggedIn && !isDemoFlight() && hasVideo() && videoCoversRange();

function updateMarkUi() {
  const r = marking.range;
  const len = r ? r[1] - r[0] : 0;
  const sf = rangeFeatures();
  $('markRangeLabel').textContent = r
    ? `${fmtTime(r[0])} – ${fmtTime(r[1])} (${len.toFixed(2)} s)` +
      (sf ? ` · Roll ${Math.round(sf.angles[0])}° · Pitch ${Math.round(sf.angles[1])}° · Yaw ${Math.round(sf.angles[2])}°` : '')
    : '–';
  $<HTMLButtonElement>('btnMarkPlay').disabled = !r;
  const isNew = markType.value === '__new';
  $('markNewFields').hidden = !isNew;
  markSend.disabled = !canSend();
  if (!canSend()) markSend.checked = false;
  $('markLoginHint').hidden = !catalog.online || catalog.loggedIn;
  $('markDemoHint').hidden = !isDemoFlight();
  const videoHint = $('markVideoHint');
  videoHint.hidden = isDemoFlight() || !catalog.online || !catalog.loggedIn || (hasVideo() && videoCoversRange());
  videoHint.textContent = hasVideo()
    ? 'Der markierte Bereich liegt außerhalb des Videos – Versatz im Tab „Video-Sync“ prüfen.'
    : 'Senden an den Admin geht nur mit Video: das Flugvideo laden und im Tab „Video-Sync“ synchronisieren. Ohne Video bleibt die Markierung lokal.';
  const nameOk = !isNew || ($<HTMLInputElement>('markNewName').value.trim().length >= 2 && markSend.checked);
  $<HTMLButtonElement>('btnMarkSave').disabled = !r || len < MARK_MIN || len > MARK_MAX || !markType.value || !nameOk || !sf;
  $<HTMLButtonElement>('btnMarkSave').title =
    len > MARK_MAX ? `Höchstens ${MARK_MAX} Sekunden markieren.`
    : isNew && !markSend.checked ? 'Neue Tricks werden beim Admin beantragt – dafür muss „An Bollisoft senden“ an sein (mit Konto).'
    : '';
}

$('btnMark').addEventListener('click', () => setMarking(!timeline.marking));
$('btnMarkClose').addEventListener('click', () => setMarking(false));
$('btnMarkStart').addEventListener('click', () => {
  const t = logTime();
  const end = marking.range ? marking.range[1] : t + 1;
  setMarkRange(t, end > t + MARK_MIN ? end : t + 1);
  timeline.reveal(marking.range![0], marking.range![1]);
});
$('btnMarkEnd').addEventListener('click', () => {
  const t = logTime();
  const start = marking.range ? marking.range[0] : t - 1;
  setMarkRange(start < t - MARK_MIN ? start : t - 1, t);
  timeline.reveal(marking.range![0], marking.range![1]);
});
$('btnMarkPlay').addEventListener('click', () => {
  if (!marking.range) return;
  seek(Math.max(0, marking.range[0] - 1));
  marking.playUntil = marking.range[1] + 0.5;
  setPlaying(true);
});
markType.addEventListener('change', updateMarkUi);
markSend.addEventListener('change', updateMarkUi);
$('markNewName').addEventListener('input', updateMarkUi);

$('btnMarkSave').addEventListener('click', async () => {
  const f = player.flight, r = marking.range, sf = rangeFeatures();
  if (!f || !r || !sf || submitting) return;
  const choice = markType.value;
  const isNew = choice === '__new';
  const newName = $<HTMLInputElement>('markNewName').value.trim();
  const send = markSend.checked && canSend();
  const mark: Mark = {
    id: ++markSeq,
    start: r[0],
    end: r[1],
    type: isNew ? '' : choice,
    name: isNew ? newName : trickLabel(choice),
    features: sf.features,
    status: send ? 'sending' : 'local',
  };
  marking.marks.push(mark);
  // local model learns right away (known tricks only – a request has no key yet)
  if (!isNew) ml.add(sf.features, choice);
  setMarkRange(null);
  reanalyze();
  if (!send) {
    toast(`„${mark.name}“ markiert (nur lokal${ml.enabled ? '' : ' – zählt als Trainingsbeispiel, sobald „ML-Klassifizierung“ aktiv ist'}).`);
    return;
  }
  const meta = {
    label: isNew ? '' : choice,
    newName: isNew ? newName : '',
    newDescription: isNew ? $<HTMLInputElement>('markNewDesc').value.trim() : '',
    comment: $<HTMLInputElement>('markComment').value.trim(),
    start: +r[0].toFixed(3),
    end: +r[1].toFixed(3),
    features: sf.features.map((v) => +v.toFixed(5)),
    angles: sf.angles.map((v) => Math.round(v)),
    peakRate: Math.round(sf.peak),
    meanThrottle: +sf.meanThrottle.toFixed(3),
    logName: state.logName || f.info.fileName,
    craft: f.info.craft,
    source: f.info.source,
    clipStart: 0,
  };
  $('submitTitle').textContent = 'Markierung senden';
  if (!hasVideo()) return toast('Senden an den Admin geht nur mit geladenem Flugvideo.', 'error');
  $('submitText').textContent =
    `Der markierte Bereich wird mit je einer Sekunde Vor- und Nachlauf einmal abgespielt und als kurzer Ausschnitt aufgenommen ` +
    `(${(r[1] - r[0] + 2).toFixed(1)} s) – nur diesen sieht der Admin bei der Prüfung.`;
  $('submitCancel').hidden = false;
  $('submitView').hidden = true;
  $('submitClose').hidden = true;
  submitPhase('Aufnahme startet …', 0);
  submitDialog.showModal();
  submitting = true;
  $('submitCancel').onclick = () => subRec?.cancel();
  try {
    const range: [number, number] = [Math.max(0, r[0] - 1), Math.min(duration(), r[1] + 1)];
    const { blob, videoStart } = await recordFlight((v) => submitPhase(`Aufnahme läuft … ${Math.round(v * 100)} %`, v * 0.6), {
      maxHeight: 720, bitrate: 2_500_000, preferMp4: false, range, audio: 'original',
    });
    meta.clipStart = +videoStart.toFixed(3);
    submitPhase(`Lade hoch (${(blob.size / 1048576).toFixed(1)} MB) …`, 0.6);
    const res = await submitMark(meta, blob, (v) => submitPhase(`Lade hoch … ${Math.round(v * 100)} %`, 0.6 + v * 0.4));
    mark.status = 'sent';
    if (res.type) {
      // new request: known locally from now on (own requests are listed in the dropdown)
      const others = getCustomTricks().filter((c) => c.key !== res.type!.key);
      registerCustomTricks([...others, res.type as CustomTrick]);
      catalog.mine = [...catalog.mine.filter((c) => c.key !== res.type!.key), ...(res.type.pending ? [res.type] : [])];
      renderMarkTypes();
    }
    mark.type = res.label.label;
    mark.name = res.label.name;
    if (isNew) ml.add(mark.features, mark.type);
    $<HTMLInputElement>('markNewName').value = '';
    $<HTMLInputElement>('markNewDesc').value = '';
    $<HTMLInputElement>('markComment').value = '';
    markType.value = '';
    submitPhase(
      isNew ? `Gesendet – der neue Trick „${mark.name}“ ist beantragt. Nach der Freigabe erscheint er für alle in der Liste.`
      : 'Gesendet – nach der Freigabe durch den Admin trainiert die Markierung das Community-Modell.',
      1,
    );
    void refreshOwnMarks();
  } catch (err) {
    mark.status = 'error';
    mark.error = (err as Error).message;
    if (err instanceof SubmitRejectedError && err.loginRequired) {
      catalog.loggedIn = false;
      auth = { ...auth, required: true, loggedIn: false, loginUrl: err.loginUrl || auth.loginUrl };
      showAuth();
    }
    submitPhase((err as Error).message || 'Senden fehlgeschlagen.', 0);
  } finally {
    submitting = false;
    $('submitCancel').hidden = true;
    $('submitClose').hidden = false;
    reanalyze();
    updateMarkUi();
  }
});
renderMarkTypes();
renderCommunity();
void loadCatalog().then(() => refreshOwnMarks());

// ---------- leaderboard entry: video, tricks, report, admin ----------
const entryDialog = $<HTMLDialogElement>('entryDialog');
const entryVideo = $<HTMLVideoElement>('entryVideo');
entryDialog.addEventListener('close', () => {
  entryVideo.pause();
  if (new URLSearchParams(location.search).has('eintrag')) history.replaceState(null, '', location.pathname);
});
$('entryClose').addEventListener('click', () => entryDialog.close());

const fmtDate = (iso: string | undefined) => (iso ? new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : '–');

async function openEntry(id: number | string) {
  let e: EntryDetail;
  try {
    e = await fetchEntry(id);
  } catch (err) {
    toast((err as Error).message, 'error');
    return;
  }
  $('entryTitle').replaceChildren(`Platz ${e.rank ?? '–'} · ${e.pilot}`, ...(e.verified ? [' ', el('span', { className: 'badge user', textContent: '✓ geprüft' })] : []));
  entryVideo.hidden = !e.videoUrl;
  $('entryNoVideo').hidden = !!e.videoUrl;
  const dl = $<HTMLAnchorElement>('entryDownload');
  dl.hidden = !e.videoUrl;
  if (e.videoUrl) {
    dl.href = e.videoUrl;
    dl.download = `${e.pilot.replace(/[^\w.-]+/g, '_')}-${e.score}.webm`;
  }
  if (e.videoUrl) {
    if (!entryVideo.src.endsWith(e.videoUrl)) entryVideo.src = e.videoUrl;
  } else entryVideo.removeAttribute('src');

  const profile = e.username ? el('a', { href: `/profil/${encodeURIComponent(e.username)}`, textContent: e.username }) : null;
  const rows: [string, string | Node][] = [
    ['Punkte', e.score.toLocaleString('de-DE')],
    ['Tricks', `${e.tricks} (bester: ${e.best || '–'})`],
    ['Max. Combo', e.maxCombo ? `${e.maxCombo}×` : '–'],
    ['Copter', e.craft || '–'],
    ['Flugdauer', fmtTime(e.duration)],
    ['Eingereicht', fmtDate(e.createdAt)],
  ];
  if (profile) rows.splice(0, 0, ['Mitglied', profile]);
  $('entryStats').replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { textContent: k }), el('dd', {}, v)]));

  $('entryTricks').replaceChildren(
    ...(e.trickList.length
      ? e.trickList.map((t) => {
          const at = Math.max(0, t.start - e.videoStart);
          const btn = el('button', { type: 'button', className: 'entry-trick' },
            el('span', { className: 'time', textContent: fmtTime(at) }),
            el('span', { textContent: t.name }),
            el('span', { className: 'pts', textContent: `${t.points.toLocaleString('de-DE')} P${t.comboMultiplier > 1 ? ` · ×${t.comboMultiplier}` : ''}` }),
          );
          btn.disabled = !e.videoUrl;
          btn.title = e.videoUrl ? 'Im Video zu diesem Trick springen' : '';
          btn.addEventListener('click', () => {
            entryVideo.currentTime = at;
            void entryVideo.play().catch(() => {});
          });
          return el('li', {}, btn);
        })
      : [el('li', { className: 'muted small', textContent: 'Keine Trickliste gespeichert.' })]),
  );

  renderEntryReport(e);
  renderEntryAdmin(e);
  if (!entryDialog.open) entryDialog.showModal();
  history.replaceState(null, '', `${location.pathname}?eintrag=${e.id}`);
}

function renderEntryReport(e: EntryDetail) {
  const host = $('entryReport');
  if (e.mine) return host.replaceChildren(el('p', { className: 'muted small', textContent: 'Das ist dein Eintrag.' }));
  if (!e.loggedIn) {
    return host.replaceChildren(el('p', { className: 'muted small' }, 'Stimmt etwas nicht? ', el('a', { href: e.loginUrl, textContent: 'Einloggen' }), ' und das Video melden.'));
  }
  if (e.reported) return host.replaceChildren(el('p', { className: 'muted small', textContent: 'Du hast diesen Eintrag gemeldet – danke, der Admin schaut ihn sich an.' }));
  const reason = el('textarea', { rows: 3, maxLength: 500, placeholder: 'Was stimmt nicht? z. B. „Tricks im Video passen nicht zur Liste“' });
  const send = el('button', { type: 'button', className: 'btn danger', textContent: 'Meldung senden' });
  const box = el('div', { className: 'entry-report-form', hidden: true }, reason, el('div', { className: 'row' }, send));
  const open = el('button', { type: 'button', className: 'btn tiny danger', textContent: '⚑ Video melden' });
  open.addEventListener('click', () => {
    box.hidden = false;
    open.hidden = true;
    reason.focus();
  });
  send.addEventListener('click', async () => {
    send.disabled = true;
    try {
      await reportEntry(e.id, reason.value.trim());
      toast('Gemeldet – danke!');
      await openEntry(e.id);
    } catch (err) {
      toast((err as Error).message, 'error');
      send.disabled = false;
    }
  });
  host.replaceChildren(open, box);
}

function renderEntryAdmin(e: EntryDetail) {
  const host = $('entryAdmin');
  host.hidden = !auth.isAdmin || !e.reports;
  if (host.hidden) return host.replaceChildren();
  const reports = e.reports ?? [];
  const verify = el('button', { type: 'button', className: 'btn tiny', textContent: e.verified ? 'Prüfung zurücknehmen' : '✓ Als geprüft markieren' });
  verify.addEventListener('click', async () => {
    try {
      await verifyEntry(e.id, !e.verified);
      await openEntry(e.id);
      refreshBoard();
    } catch (err) {
      toast((err as Error).message, 'error');
    }
  });
  const openLog = el('button', { type: 'button', className: 'btn tiny', textContent: 'Log im Viewer prüfen' });
  openLog.disabled = !e.logUrl;
  openLog.title = e.logUrl ? 'Lädt das eingereichte Log mit den Erkennungs-Einstellungen des Einreichers' : 'Kein Log gespeichert';
  openLog.addEventListener('click', async () => {
    try {
      const res = await fetch(e.logUrl!, { cache: 'no-store' });
      if (!res.ok) throw new Error('Log konnte nicht geladen werden.');
      const blob = await res.blob();
      entryDialog.close();
      player.settings = { ...DEFAULT_SETTINGS, ...(e.settings as Partial<DetectSettings>) };
      renderSettings();
      pendingLogIndex = e.logIndex ?? null;
      await loadLogFile(blob, e.logName || `eintrag-${e.id}.bbl`);
      toast(`Log von ${e.pilot} geladen – mit dessen Erkennungs-Einstellungen. Eingereicht: ${e.score.toLocaleString('de-DE')} Punkte.`, 'info', 8000);
    } catch (err) {
      toast((err as Error).message, 'error');
    }
  });
  const del = el('button', { type: 'button', className: 'btn tiny danger', textContent: 'Eintrag löschen' });
  del.addEventListener('click', async () => {
    if (!confirm(`Eintrag von „${e.pilot}“ (${e.score} Punkte) mit Video endgültig löschen?`)) return;
    try {
      await deleteEntry(e.id);
      entryDialog.close();
      toast('Eintrag gelöscht.');
      refreshBoard();
    } catch (err) {
      toast((err as Error).message, 'error');
    }
  });
  const account = e.account?.email ? `${e.account.username ?? '–'} <${e.account.email}>` : 'ohne Konto (alter Eintrag)';
  host.replaceChildren(
    el('h3', { textContent: 'Admin' }),
    el('p', { className: 'small', textContent: `Konto: ${account} · Log: ${e.logName || '–'}${e.logIndex ? ` (Log ${e.logIndex + 1})` : ''}` }),
    el('p', { className: 'small' }, el('b', { textContent: `${reports.length} Meldung${reports.length === 1 ? '' : 'en'}` }),
      ` (E-Mail ab ${e.reportThreshold ?? 10}${e.alertSentAt ? `, gesendet ${fmtDate(e.alertSentAt)}` : ''})`),
    ...(reports.length
      ? [el('ol', { className: 'entry-reports' }, ...reports.map((r) => el('li', {},
          el('b', { textContent: r.username ?? '–' }), ` <${r.email}> · ${fmtDate(r.createdAt)}`,
          ...(r.reason ? [el('br'), el('span', { className: 'muted', textContent: r.reason })] : []))))]
      : []),
    el('div', { className: 'row wrap' }, verify, openLog, el('span', { className: 'spacer' }), del),
  );
}

// Direct link from the admin e-mail / dashboard: ?eintrag=<id>
{
  const deep = new URLSearchParams(location.search).get('eintrag');
  if (deep) void refreshAuth().then(() => openEntry(deep));
}
$('btnBoardRefresh').addEventListener('click', refreshBoard);
$('boardCells').addEventListener('change', refreshBoard);

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
