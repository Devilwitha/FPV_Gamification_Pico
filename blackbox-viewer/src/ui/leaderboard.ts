// Extension: online leaderboard (api/leaderboard, relative to the page so it also works under a sub-path) with a local fallback when no server is reachable.
export interface ScoreEntry {
  id?: string;
  /** Log type of the flight ('bbl' | 'csv'); the server rejects demo flights. */
  source?: string;
  pilot: string;
  score: number;
  tricks: number;
  best: string;
  maxCombo: number;
  craft: string;
  duration: number;
  createdAt?: string;
}

const LOCAL_KEY = 'bba.leaderboard.local';

function readLocal(): ScoreEntry[] {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) ?? '[]');
  } catch {
    return [];
  }
}

function writeLocal(list: ScoreEntry[]) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(list.slice(0, 100)));
  } catch {
    /* ignore */
  }
}

export async function fetchLeaderboard(): Promise<{ entries: ScoreEntry[]; online: boolean }> {
  try {
    const res = await fetch('api/leaderboard?limit=50', { headers: { Accept: 'application/json' } });
    if (!res.ok || !res.headers.get('content-type')?.includes('json')) throw new Error();
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
}

/** The server rejected a submission (not logged in, invalid entry such as a demo flight, or the login service is unavailable). */
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
    if (!res.ok || !res.headers.get('content-type')?.includes('json')) throw new Error();
    const data = await res.json();
    return {
      required: data.required === true,
      loggedIn: data.loggedIn === true,
      loginUrl: typeof data.loginUrl === 'string' ? data.loginUrl : '',
    };
  } catch {
    // No server (or no login support): submissions fall back to the local leaderboard.
    return { required: false, loggedIn: true, loginUrl: '' };
  }
}

function saveLocal(entry: ScoreEntry): { rank: number; online: boolean } {
  const list = readLocal();
  const local = { ...entry, id: crypto.randomUUID(), createdAt: new Date().toISOString() };
  list.push(local);
  list.sort((a, b) => b.score - a.score);
  writeLocal(list);
  return { rank: list.findIndex((e) => e.id === local.id) + 1, online: false };
}

/**
 * Throws SubmitRejectedError if the server refuses the entry (login requirement, demo flight, …).
 * Only an unreachable server (or a non-JSON answer) falls back to the local leaderboard.
 */
export async function submitScore(entry: ScoreEntry): Promise<{ rank: number; online: boolean }> {
  let res: Response;
  try {
    res = await fetch('api/leaderboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    });
  } catch {
    return saveLocal(entry);
  }
  const isJson = res.headers.get('content-type')?.includes('json') ?? false;
  if (isJson && !res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new SubmitRejectedError(
      typeof data.error === 'string' ? data.error : 'Einreichen nicht möglich.',
      res.status === 401,
      typeof data.loginUrl === 'string' ? data.loginUrl : '',
    );
  }
  if (!res.ok || !isJson) return saveLocal(entry);
  const data = await res.json();
  return { rank: data.rank, online: true };
}
