// VS viewer: two FlightPlayers side by side on one shared duel clock.
// Duel time T = 0 is the take-off of each pilot; side log time = windowStart + shift + T.
//  - ?match=<id>: replay of both submitted clips (HUD burned in) from the website, live score from the
//    stored trick lists (logs stay private on the server);
//  - without parameter: compare two local logs (optionally with video) – analysis, HUD and the duel
//    window come from the same core as the single viewer.
import './styles.css';
import './vs.css';
import { DEFAULT_SETTINGS, type DetectSettings } from './analysis/tricks.ts';
import { decodeFlight } from './core/decoder.ts';
import { liveScore, takeoffTime, windowScore, type ScoredTrick } from './core/match.ts';
import { FlightPlayer } from './core/player.ts';
import { audioEnvelope, correlate, estimateDrift, logSignal } from './sync/audio.ts';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const fmtTime = (t: number) => {
  const s = Math.max(0, t);
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};
const fmtPts = (n: number) => n.toLocaleString('de-CH');
let toastTimer = 0;
function toast(msg: string, kind: 'info' | 'error' = 'info', ms = 5000) {
  const t = $('toast');
  t.textContent = msg;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (t.hidden = true), ms);
}
function busy(label: string | null, value = 0) {
  $('busy').hidden = label === null;
  if (label !== null) {
    $('busyLabel').textContent = label;
    $('busyBar').style.width = `${Math.round(value * 100)}%`;
  }
}
function loadSettings(): DetectSettings {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem('bba.settings') ?? '{}') };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

type SideKey = 'a' | 'b';
interface Side {
  key: SideKey;
  player: FlightPlayer;
  name: string;
  /** log time of the take-off (start of the duel window) */
  windowStart: number;
  /** manual correction of the start (s) */
  shift: number;
  tricks: ScoredTrick[];
  ready: boolean;
  lastShown: string;
}

const settings = loadSettings();
const mk = (key: SideKey): Side => {
  const up = key.toUpperCase();
  const player = new FlightPlayer($<HTMLCanvasElement>(`canvas${up}`), $<HTMLVideoElement>(`video${up}`), { settings });
  player.video.muted = true;
  return { key, player, name: key === 'a' ? 'Pilot A' : 'Pilot B', windowStart: 0, shift: 0, tricks: [], ready: false, lastShown: '' };
};
const sides: Record<SideKey, Side> = { a: mk('a'), b: mk('b') };
const both = () => [sides.a, sides.b];

const clock = { t: -1, playing: false, rate: 1, limit: 120, finished: false };
const LEAD_IN = 1;

// ---------- duel clock ----------
function logTimeOf(side: Side, t = clock.t) {
  return side.windowStart + side.shift + t;
}

function syncSide(side: Side, hard: boolean) {
  const p = side.player;
  if (!side.ready) return;
  const lt = logTimeOf(side);
  if (p.hasVideo()) {
    const vt = p.toVideoTime(lt);
    const inside = vt >= 0 && vt <= (p.video.duration || 0);
    if (hard || Math.abs(p.video.currentTime - vt) > 0.25) p.video.currentTime = Math.max(0, Math.min(p.video.duration || 0, vt));
    p.video.playbackRate = clock.rate;
    if (clock.playing && inside && p.video.paused) p.video.play().catch(() => {});
    if ((!clock.playing || !inside) && !p.video.paused) p.video.pause();
  } else {
    p.clockT = lt;
  }
}

function seekDuel(t: number) {
  clock.t = Math.max(-LEAD_IN, Math.min(clock.limit, t));
  clock.finished = clock.t >= clock.limit;
  for (const s of both()) syncSide(s, true);
  render(true);
}

function setPlaying(on: boolean) {
  if (on && clock.t >= clock.limit) seekDuel(-LEAD_IN);
  clock.playing = on && both().some((s) => s.ready);
  $('vsPlay').textContent = clock.playing ? '❚❚' : '▶';
  for (const s of both()) syncSide(s, false);
}

let lastNow = performance.now();
function frame(now: number) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.1, (now - lastNow) / 1000);
  lastNow = now;
  if (clock.playing) {
    clock.t += dt * clock.rate;
    if (clock.t >= clock.limit) {
      clock.t = clock.limit;
      clock.finished = true;
      setPlaying(false);
    }
    for (const s of both()) syncSide(s, false);
  }
  render(false);
}

// ---------- rendering ----------
function render(force: boolean) {
  const scores: Record<SideKey, number> = { a: 0, b: 0 };
  for (const s of both()) {
    const lt = logTimeOf(s);
    s.player.render(lt);
    const live = liveScore(s.tricks, lt);
    scores[s.key] = live.total;
    const up = s.key.toUpperCase();
    const shown = `${live.total}|${live.last?.name ?? ''}|${live.count}`;
    if (force || shown !== s.lastShown) {
      s.lastShown = shown;
      $(`score${up}`).textContent = fmtPts(live.total);
      $(`last${up}`).textContent = live.last ? `${live.last.name} +${live.last.points}` : '';
      const done = new Set(s.tricks.filter((t) => t.end <= lt));
      for (const li of $(`tricks${up}`).children) li.classList.toggle('done', done.has(s.tricks[Number((li as HTMLElement).dataset.i)]));
    }
  }
  const total = scores.a + scores.b;
  $('barA').style.width = `${total ? (scores.a / total) * 100 : 50}%`;
  $('barB').style.width = `${total ? (scores.b / total) * 100 : 50}%`;
  for (const k of ['a', 'b'] as SideKey[]) {
    const other = k === 'a' ? 'b' : 'a';
    document.querySelector(`.vs-team[data-side="${k}"]`)!.classList.toggle('lead', scores[k] > scores[other]);
  }
  $('vsClock').textContent = clock.t < 0 ? `−${fmtTime(-clock.t)} / ${fmtTime(clock.limit)}` : `${fmtTime(clock.t)} / ${fmtTime(clock.limit)}`;
  const scrub = $<HTMLInputElement>('vsScrub');
  if (document.activeElement !== scrub) scrub.value = String(clock.t);
  const ready = both().filter((s) => s.ready);
  $('vsResult').textContent =
    clock.finished && ready.length === 2
      ? scores.a === scores.b ? 'Unentschieden' : `🏆 ${scores.a > scores.b ? sides.a.name : sides.b.name} gewinnt`
      : '';
}

function renderTrickList(side: Side) {
  const list = $(`tricks${side.key.toUpperCase()}`);
  list.replaceChildren(
    ...side.tricks.map((t, i) => {
      const li = document.createElement('li');
      li.dataset.i = String(i);
      const at = t.start - side.windowStart;
      li.innerHTML = '<span></span><b></b>';
      li.firstElementChild!.textContent = `${fmtTime(at)} ${t.name}`;
      li.lastElementChild!.textContent = `+${t.points}`;
      li.addEventListener('click', () => seekDuel(at - 1));
      return li;
    }),
  );
  if (!side.tricks.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = side.ready ? 'Keine Tricks in der Wertungszeit.' : '';
    list.append(li);
  }
}

function sideReady(side: Side) {
  side.ready = true;
  const up = side.key.toUpperCase();
  $(`name${up}`).textContent = side.name;
  $(`empty${up}`).hidden = true;
  $<HTMLButtonElement>('vsPlay').disabled = false;
  renderTrickList(side);
  seekDuel(clock.t);
}

function setLimit(limit: number) {
  clock.limit = limit;
  $<HTMLInputElement>('vsScrub').max = String(limit);
  for (const s of both()) if (s.player.flight && s.player.analysis) applyWindow(s);
  seekDuel(Math.min(clock.t, limit));
}

// ---------- local mode: own logs ----------
const fullAnalysis: Partial<Record<SideKey, ReturnType<FlightPlayer['analyze']>>> = {};

function applyWindow(side: Side) {
  const f = side.player.flight!;
  const full = fullAnalysis[side.key]!;
  side.windowStart = takeoffTime(f, full);
  const win = windowScore(full, settings, side.windowStart, clock.limit);
  side.tricks = win.tricks;
  // HUD shows the duel score: only the window tricks
  side.player.analysis = { ...full, tricks: win.tricks };
  renderTrickList(side);
}

async function loadLocalLog(side: Side, file: File) {
  busy('Dekodiere…', 0);
  try {
    const { flight } = await decodeFlight(file, file.name, undefined, (label, v) => busy(label, v));
    side.player.setFlight(flight);
    fullAnalysis[side.key] = side.player.analyze();
    side.name = flight.info.craft || file.name.replace(/\.[^.]+$/, '');
    side.shift = 0;
    updateShiftLabel(side);
    applyWindow(side);
    updateSyncButton(side);
    info(side);
    sideReady(side);
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
  } finally {
    busy(null);
  }
}

async function loadLocalVideo(side: Side, file: File) {
  try {
    await side.player.loadVideo(file);
    side.player.video.muted = $<HTMLInputElement>('vsMute').checked;
    updateSyncButton(side);
    info(side);
    syncSide(side, true);
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error');
  }
}

async function autoSync(side: Side) {
  const p = side.player;
  if (!p.flight || !p.videoFile) return;
  busy('Audio ↔ Motor korrelieren…', 0.3);
  try {
    const env = await audioEnvelope(p.videoFile, (m) => busy(m, 0.5));
    const sig = logSignal(p.flight);
    const res = correlate(env, sig);
    const drift = res.confidence >= 5 ? estimateDrift(env, sig, res.offset) : null;
    p.setSync(drift ? drift.offset : res.offset, drift ? drift.speed : 1);
    toast(`${side.name}: Versatz ${p.offset.toFixed(2)} s${drift ? `, Drift ${((p.speed - 1) * 100).toFixed(2)} %` : ''} · Konfidenz ${res.confidence.toFixed(1)}σ`);
    info(side);
    syncSide(side, true);
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error', 7000);
  } finally {
    busy(null);
  }
}

function info(side: Side) {
  const p = side.player;
  const parts: string[] = [];
  if (p.flight) parts.push(`Abheben bei ${side.windowStart.toFixed(1)} s`);
  if (p.videoFile) parts.push(p.offset || p.speed !== 1 ? `Video-Versatz ${p.offset.toFixed(2)} s` : 'Video ohne Sync');
  (document.querySelector(`[data-info="${side.key}"]`) as HTMLElement).textContent = parts.join(' · ');
}

function updateSyncButton(side: Side) {
  (document.querySelector(`[data-autosync="${side.key}"]`) as HTMLButtonElement).disabled = !(side.player.flight && side.player.videoFile);
}

function updateShiftLabel(side: Side) {
  const v = side.shift;
  (document.querySelector(`[data-shiftlabel="${side.key}"]`) as HTMLElement).textContent = `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} s`;
}

// ---------- match mode: clips from the website ----------
interface MatchEntry {
  pilot: string;
  score: number;
  windowStart: number;
  videoStart: number;
  videoUrl?: string | null;
  trickList?: ScoredTrick[];
}
interface MatchData {
  id: number;
  limit: number;
  status: string;
  winner: string | null;
  draw: boolean;
  a: { username: string } | null;
  b: { username: string } | null;
  entries: Partial<Record<SideKey, MatchEntry>>;
  pageUrl: string;
  tournament: { name: string } | null;
}

async function loadMatch(id: number) {
  busy('Lade Duell…', 0.2);
  try {
    const res = await fetch(`../api/vs/matches/${id}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) throw new Error(res.status === 404 ? 'Duell nicht gefunden.' : `Duell konnte nicht geladen werden (HTTP ${res.status}).`);
    const m: MatchData = (await res.json()).match;
    setLimit(m.limit);
    $('vsSubtitle').textContent = `${m.a?.username ?? '?'} vs ${m.b?.username ?? '?'}${m.tournament ? ` · ${m.tournament.name}` : ''}`;
    const link = $<HTMLAnchorElement>('matchLink');
    link.href = m.pageUrl;
    link.hidden = false;
    for (const key of ['a', 'b'] as SideKey[]) {
      const side = sides[key];
      side.name = (key === 'a' ? m.a?.username : m.b?.username) ?? '–';
      $(`name${key.toUpperCase()}`).textContent = side.name;
      const e = m.entries[key];
      if (!e || !e.videoUrl) {
        $(`empty${key.toUpperCase()}`).textContent = e ? 'Flug wartet auf Bestätigung' : 'Flug noch nicht eingereicht';
        continue;
      }
      side.windowStart = e.windowStart;
      side.tricks = e.trickList ?? [];
      side.player.setReplay(e.windowStart + m.limit + 2, e.videoStart);
      await side.player.loadVideo(e.videoUrl);
      side.player.video.muted = $<HTMLInputElement>('vsMute').checked;
      sideReady(side);
    }
    if (m.status === 'done') toast(m.draw ? 'Ergebnis: Unentschieden' : `Ergebnis: ${m.winner} gewinnt`);
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), 'error', 8000);
  } finally {
    busy(null);
  }
}

// ---------- controls ----------
$('vsPlay').addEventListener('click', () => setPlaying(!clock.playing));
$('vsBack').addEventListener('click', () => seekDuel(clock.t - 2));
$('vsFwd').addEventListener('click', () => seekDuel(clock.t + 2));
$<HTMLInputElement>('vsScrub').addEventListener('input', (e) => seekDuel(Number((e.target as HTMLInputElement).value)));
$<HTMLSelectElement>('vsSpeed').addEventListener('change', (e) => {
  clock.rate = Number((e.target as HTMLSelectElement).value);
  for (const s of both()) syncSide(s, false);
});
$<HTMLSelectElement>('vsLimit').addEventListener('change', (e) => setLimit(Number((e.target as HTMLSelectElement).value)));
$<HTMLInputElement>('vsMute').addEventListener('change', (e) => {
  for (const s of both()) s.player.video.muted = (e.target as HTMLInputElement).checked;
});
document.querySelectorAll<HTMLButtonElement>('[data-shift]').forEach((b) =>
  b.addEventListener('click', () => {
    const side = sides[b.dataset.shift as SideKey];
    side.shift = Math.round((side.shift + Number(b.dataset.delta)) * 10) / 10;
    updateShiftLabel(side);
    syncSide(side, true);
    render(true);
  }),
);
document.querySelectorAll<HTMLInputElement>('[data-log]').forEach((input) =>
  input.addEventListener('change', () => {
    const f = input.files?.[0];
    input.value = '';
    if (f) void loadLocalLog(sides[input.dataset.log as SideKey], f);
  }),
);
document.querySelectorAll<HTMLInputElement>('[data-video]').forEach((input) =>
  input.addEventListener('change', () => {
    const f = input.files?.[0];
    input.value = '';
    if (f) void loadLocalVideo(sides[input.dataset.video as SideKey], f);
  }),
);
document.querySelectorAll<HTMLButtonElement>('[data-autosync]').forEach((b) =>
  b.addEventListener('click', () => void autoSync(sides[b.dataset.autosync as SideKey])),
);
document.addEventListener('keydown', (e) => {
  const tag = (e.target as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
  if (e.code === 'Space') {
    e.preventDefault();
    setPlaying(!clock.playing);
  } else if (e.code === 'ArrowLeft') seekDuel(clock.t - (e.shiftKey ? 0.1 : 2));
  else if (e.code === 'ArrowRight') seekDuel(clock.t + (e.shiftKey ? 0.1 : 2));
});

// ---------- start ----------
const matchId = Number(new URLSearchParams(location.search).get('match'));
if (matchId) {
  void loadMatch(matchId);
} else {
  $('vsSubtitle').textContent = 'Zwei eigene Flüge vergleichen';
  $('localA').hidden = false;
  $('localB').hidden = false;
  $('limitWrap').hidden = false;
  $('emptyA').textContent = 'Log (und Video) für Pilot A laden';
  $('emptyB').textContent = 'Log (und Video) für Pilot B laden';
}
seekDuel(-LEAD_IN);
requestAnimationFrame(frame);
