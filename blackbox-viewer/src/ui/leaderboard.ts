// Online leaderboard (api/leaderboard, relative to the page so it also works under a sub-path).
// On bollisoft.ch the API is served by the webshop (webshop/leaderboard.py): submissions carry the
// recorded video with overlay plus the log, entries can be reported, and the admin verifies/deletes.
// Without a server the previously stored local entries are shown read-only.
export interface ScoreEntry {
  id?: number | string;
  /** Log type of the flight ('bbl' | 'csv'); the server rejects demo flights. */
  source?: string;
  pilot: string;
  /** Forum username of the submitting account (public profile), if any. */
  username?: string | null;
  /** ISO country code from the account (empty when hidden in the profile). */
  country?: string | null;
  score: number;
  tricks: number;
  best: string;
  maxCombo: number;
  craft: string;
  duration: number;
  createdAt?: string;
  rank?: number;
  hasVideo?: boolean;
  verified?: boolean;
  mine?: boolean;
  /** Only sent to the admin. */
  reportCount?: number;
}

export interface EntryTrick {
  type: string;
  name: string;
  count: number;
  start: number;
  end: number;
  points: number;
  comboMultiplier: number;
  source: string;
}

export interface EntryReport {
  username: string | null;
  email: string;
  reason: string;
  createdAt: string;
}

export interface EntryDetail extends ScoreEntry {
  id: number;
  trickList: EntryTrick[];
  videoUrl: string | null;
  videoStart: number;
  reported: boolean;
  canReport: boolean;
  loggedIn: boolean;
  loginUrl: string;
  // admin only
  account?: { email: string | null; username: string | null };
  logUrl?: string | null;
  logName?: string;
  logIndex?: number;
  settings?: Record<string, unknown>;
  reports?: EntryReport[];
  reportThreshold?: number;
  alertSentAt?: string | null;
}

/** Everything the server needs besides the files. */
export interface SubmitMeta extends ScoreEntry {
  logName: string;
  logIndex: number;
  trickList: EntryTrick[];
  settings: Record<string, unknown>;
  /** Log time (s) at the first frame of the recorded video. */
  videoStart: number;
}

const LOCAL_KEY = 'bba.leaderboard.local';
const AJAX = { 'X-Requested-With': 'blackbox-viewer' };
const isJson = (res: Response) => res.headers.get('content-type')?.includes('json') ?? false;

function readLocal(): ScoreEntry[] {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) ?? '[]');
  } catch {
    return [];
  }
}

export async function fetchLeaderboard(): Promise<{ entries: ScoreEntry[]; online: boolean }> {
  try {
    const res = await fetch('api/leaderboard?limit=50', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok || !isJson(res)) throw new Error();
    const data = await res.json();
    return { entries: data.entries ?? [], online: true };
  } catch {
    return { entries: readLocal(), online: false };
  }
}

/** Whether the server requires a login for submitting, and whether the visitor has one. */
export interface AuthState {
  required: boolean;
  loggedIn: boolean;
  loginUrl: string;
  isAdmin: boolean;
  username: string | null;
}

/** The server rejected a request (not logged in, invalid entry such as a demo flight, …). */
export class SubmitRejectedError extends Error {
  loginRequired: boolean;
  loginUrl: string;
  constructor(message: string, loginRequired: boolean, loginUrl: string) {
    super(message);
    this.loginRequired = loginRequired;
    this.loginUrl = loginUrl;
  }
}

export async function fetchAuth(): Promise<AuthState> {
  try {
    const res = await fetch('api/leaderboard/auth', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok || !isJson(res)) throw new Error();
    const data = await res.json();
    return {
      required: data.required === true,
      loggedIn: data.loggedIn === true,
      loginUrl: typeof data.loginUrl === 'string' ? data.loginUrl : '',
      isAdmin: data.isAdmin === true,
      username: typeof data.username === 'string' ? data.username : null,
    };
  } catch {
    return { required: false, loggedIn: true, loginUrl: '', isAdmin: false, username: null };
  }
}

async function rejection(res: Response, fallback: string): Promise<SubmitRejectedError> {
  const data = isJson(res) ? await res.json().catch(() => ({})) : {};
  return new SubmitRejectedError(
    typeof data.error === 'string' ? data.error : res.status === 404 || res.status === 413 ? 'Zu groß für den Server.' : fallback,
    res.status === 401,
    typeof data.loginUrl === 'string' ? data.loginUrl : '',
  );
}

/**
 * Upload score, recorded video (with overlay) and the log. onProgress gets 0…1 for the upload.
 * Throws SubmitRejectedError if the server refuses, Error if it is unreachable.
 */
export function submitScore(
  meta: SubmitMeta,
  video: Blob,
  log: Blob | null,
  onProgress?: (v: number) => void,
  signal?: AbortSignal,
): Promise<{ rank: number; id: number }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('meta', JSON.stringify(meta));
    form.append('video', video, video.type.includes('mp4') ? 'flug.mp4' : 'flug.webm');
    if (log) form.append('log', log, meta.logName || 'flug.bbl');
    const xhr = new XMLHttpRequest();
    xhr.open('POST', 'api/leaderboard');
    for (const [k, v] of Object.entries(AJAX)) xhr.setRequestHeader(k, v);
    if (onProgress) xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = async () => {
      const res = new Response(xhr.responseText, { status: xhr.status, headers: { 'content-type': xhr.getResponseHeader('content-type') ?? '' } });
      if (xhr.status === 201) {
        const data = await res.json();
        resolve({ rank: data.rank, id: data.entry.id });
      } else reject(await rejection(res, `Einreichen fehlgeschlagen (HTTP ${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error('Server nicht erreichbar – bitte später erneut versuchen.'));
    xhr.onabort = () => reject(new Error('Abgebrochen.'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

export async function fetchEntry(id: number | string): Promise<EntryDetail> {
  const res = await fetch(`api/leaderboard/${id}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!res.ok || !isJson(res)) throw await rejection(res, 'Eintrag nicht gefunden.');
  return (await res.json()).entry;
}

async function action(url: string, method: string, body?: unknown) {
  const res = await fetch(url, {
    method,
    headers: { ...AJAX, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await rejection(res, `Aktion fehlgeschlagen (HTTP ${res.status}).`);
}

export const reportEntry = (id: number, reason: string) => action(`api/leaderboard/${id}/report`, 'POST', { reason });
export const verifyEntry = (id: number, verified: boolean) => action(`api/leaderboard/${id}/verify`, 'POST', { verified });
export const deleteEntry = (id: number) => action(`api/leaderboard/${id}`, 'DELETE');
