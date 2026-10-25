// Trick marks and community tricks (webshop/trick_labels.py, /api/tricks).
// The viewer lives under /blackbox-viewer/, the API at the site root – hence "../api/tricks".
// Without the website (local development) the catalog is empty and marks stay local.
import type { CustomTrick } from '../analysis/tricks.ts';
import type { LabelExample } from '../analysis/learned.ts';
import { SubmitRejectedError } from './leaderboard.ts';

const API = '../api/tricks';
const AJAX = { 'X-Requested-With': 'blackbox-viewer' };
const isJson = (res: Response) => res.headers.get('content-type')?.includes('json') ?? false;

export interface TrickCatalog {
  online: boolean;
  loggedIn: boolean;
  /** approved community tricks */
  custom: CustomTrick[];
  /** own requests that are not approved yet */
  mine: CustomTrick[];
  /** approved marks: training data of the community model */
  examples: LabelExample[];
}

export interface OwnMark {
  id: number;
  label: string;
  name: string;
  status: 'pending' | 'approved' | 'rejected';
  note: string;
  start: number;
  end: number;
  logName: string;
  createdAt: string;
  hasClip: boolean;
}

/** Everything the server needs besides the clip. Either `label` or `newName` is set. */
export interface MarkMeta {
  label: string;
  newName: string;
  newDescription: string;
  comment: string;
  start: number;
  end: number;
  features: number[];
  angles: number[];
  peakRate: number;
  meanThrottle: number;
  logName: string;
  craft: string;
  source: string;
  /** log time (s) at the first frame of the clip */
  clipStart: number;
}

const toTrick = (raw: Record<string, unknown>): CustomTrick | null =>
  typeof raw.key === 'string' && typeof raw.name === 'string'
    ? {
        key: raw.key,
        name: raw.name,
        points: Number(raw.points) || 0,
        description: typeof raw.description === 'string' ? raw.description : '',
        pending: raw.pending === true,
      }
    : null;

export async function fetchCatalog(): Promise<TrickCatalog> {
  try {
    const res = await fetch(`${API}/katalog`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok || !isJson(res)) throw new Error();
    const data = await res.json();
    const list = (v: unknown) => (Array.isArray(v) ? v.map(toTrick).filter((c): c is CustomTrick => !!c) : []);
    return {
      online: true,
      loggedIn: data.loggedIn === true,
      custom: list(data.custom),
      mine: list(data.mine),
      examples: Array.isArray(data.examples)
        ? data.examples.filter((e: LabelExample) => typeof e?.label === 'string' && Array.isArray(e.f))
        : [],
    };
  } catch {
    return { online: false, loggedIn: false, custom: [], mine: [], examples: [] };
  }
}

export async function fetchOwnMarks(): Promise<OwnMark[]> {
  try {
    const res = await fetch(`${API}/markierungen`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok || !isJson(res)) return [];
    return (await res.json()).labels ?? [];
  } catch {
    return [];
  }
}

/** Upload a mark with its video clip. Resolves with the stored mark and – for a new request – its trick. */
export function submitMark(
  meta: MarkMeta,
  clip: Blob | null,
  onProgress?: (v: number) => void,
): Promise<{ label: OwnMark; type: CustomTrick | null }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('meta', JSON.stringify(meta));
    if (clip) form.append('clip', clip, clip.type.includes('mp4') ? 'ausschnitt.mp4' : 'ausschnitt.webm');
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/markierungen`);
    for (const [k, v] of Object.entries(AJAX)) xhr.setRequestHeader(k, v);
    if (onProgress) xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status === 201) {
        resolve({ label: data.label as OwnMark, type: data.type ? toTrick(data.type as Record<string, unknown>) : null });
      } else {
        const message =
          typeof data.error === 'string' ? data.error : xhr.status === 413 ? 'Zu groß für den Server.' : `Senden fehlgeschlagen (HTTP ${xhr.status}).`;
        reject(new SubmitRejectedError(message, xhr.status === 401, typeof data.loginUrl === 'string' ? data.loginUrl : ''));
      }
    };
    xhr.onerror = () => reject(new Error('Server nicht erreichbar – bitte später erneut versuchen.'));
    xhr.send(form);
  });
}

export async function deleteOwnMark(id: number): Promise<void> {
  const res = await fetch(`${API}/markierungen/${id}`, { method: 'DELETE', headers: AJAX });
  if (!res.ok) {
    const data = isJson(res) ? await res.json().catch(() => ({})) : {};
    throw new Error(typeof data.error === 'string' ? data.error : `Löschen fehlgeschlagen (HTTP ${res.status}).`);
  }
}
