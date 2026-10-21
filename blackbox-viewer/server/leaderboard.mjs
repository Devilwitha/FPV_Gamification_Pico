// Minimal leaderboard API, used both as Vite dev middleware and by server.mjs.
// Storage: a JSON file in ../data. Scores are submitted by clients and are not verified.
//
// Optional login requirement for submitting (reading stays public):
//   LEADERBOARD_AUTH_URL   – URL that reports the visitor's login state as JSON {"logged_in": bool}.
//                            It is called with the visitor's Cookie header (same domain behind a proxy).
//   LEADERBOARD_LOGIN_URL  – login page shown to visitors who are not logged in.
// Without LEADERBOARD_AUTH_URL anyone can submit (e.g. local development).
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';

const DATA_DIR = new URL('../data/', import.meta.url);
const DATA_FILE = new URL('leaderboard.json', DATA_DIR);
const MAX_ENTRIES = 500;
const MAX_BODY = 16 * 1024;
const AUTH_URL = process.env.LEADERBOARD_AUTH_URL ?? '';
const LOGIN_URL = process.env.LEADERBOARD_LOGIN_URL ?? '';
const AUTH_TIMEOUT_MS = 5000;

// Login state of the requesting visitor. Throws if the auth service is unreachable.
async function checkAuth(req) {
  if (!AUTH_URL) return { required: false, loggedIn: true, loginUrl: '' };
  const res = await fetch(AUTH_URL, {
    headers: { Accept: 'application/json', Cookie: req.headers.cookie ?? '' },
    redirect: 'manual',
    signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`Login-Dienst antwortet mit ${res.status}`);
  const data = await res.json();
  return { required: true, loggedIn: data?.logged_in === true, loginUrl: LOGIN_URL };
}

let cache = null;
let writeChain = Promise.resolve();

async function load() {
  if (cache) return cache;
  try {
    cache = JSON.parse(await readFile(DATA_FILE, 'utf8'));
    if (!Array.isArray(cache)) cache = [];
  } catch {
    cache = [];
  }
  return cache;
}

function persist() {
  writeChain = writeChain
    .then(async () => {
      await mkdir(DATA_DIR, { recursive: true });
      await writeFile(DATA_FILE, JSON.stringify(cache, null, 1));
    })
    .catch((err) => console.error('[leaderboard] write failed', err));
  return writeChain;
}

const str = (v, max) => (typeof v === 'string' ? v.trim().slice(0, max) : '');
const num = (v, min, max) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : min;
};

function sanitize(body) {
  const pilot = str(body.pilot, 32);
  if (!pilot) throw new Error('Pilotname fehlt');
  const source = str(body.source, 8);
  if (source === 'demo') throw new Error('Demo-Flüge können nicht eingereicht werden.');
  if (source !== 'bbl' && source !== 'csv') {
    throw new Error('Nur echte Blackbox-Logs (.bbl, .bfl, .csv) können eingereicht werden.');
  }
  return {
    id: randomUUID(),
    source,
    pilot,
    score: Math.round(num(body.score, 0, 10_000_000)),
    tricks: Math.round(num(body.tricks, 0, 10_000)),
    best: str(body.best, 40),
    maxCombo: Math.round(num(body.maxCombo, 0, 1000)),
    craft: str(body.craft, 40),
    duration: num(body.duration, 0, 24 * 3600),
    createdAt: new Date().toISOString(),
  };
}

function send(res, status, data) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(data));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error('Request zu groß'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

export function leaderboardMiddleware() {
  return async (req, res, next) => {
    const url = new URL(req.url ?? '/', 'http://local');
    if (url.pathname === '/api/leaderboard/auth') {
      if (req.method !== 'GET') return send(res, 405, { error: 'Methode nicht erlaubt' });
      try {
        return send(res, 200, await checkAuth(req));
      } catch (err) {
        console.error('[leaderboard] auth check failed', err);
        return send(res, 503, { error: 'Login-Status nicht abrufbar' });
      }
    }
    if (url.pathname !== '/api/leaderboard') return next ? next() : false;
    try {
      const entries = await load();
      if (req.method === 'GET') {
        const limit = Math.round(num(url.searchParams.get('limit') ?? 50, 1, 200));
        return send(res, 200, { entries: entries.slice(0, limit) });
      }
      if (req.method === 'POST') {
        let auth;
        try {
          auth = await checkAuth(req);
        } catch (err) {
          console.error('[leaderboard] auth check failed', err);
          return send(res, 503, { error: 'Login-Status nicht abrufbar – bitte später erneut versuchen.' });
        }
        if (!auth.loggedIn) {
          return send(res, 401, { error: 'Zum Einreichen bitte einloggen.', loginUrl: auth.loginUrl });
        }
        const entry = sanitize(JSON.parse(await readBody(req)));
        entries.push(entry);
        entries.sort((a, b) => b.score - a.score);
        entries.length = Math.min(entries.length, MAX_ENTRIES);
        await persist();
        const rank = entries.findIndex((e) => e.id === entry.id) + 1;
        return send(res, 201, { entry, rank });
      }
      return send(res, 405, { error: 'Methode nicht erlaubt' });
    } catch (err) {
      return send(res, 400, { error: err instanceof Error ? err.message : 'Ungültige Anfrage' });
    }
  };
}
