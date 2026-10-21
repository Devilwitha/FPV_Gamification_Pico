// Production server: serves the built app from ../dist and the leaderboard API.
// Optional env:
//   PORT       – listen port (default 8787)
//   HOST       – listen address (default: all interfaces; e.g. 127.0.0.1 behind a reverse proxy)
//   BASE_PATH  – sub-path the app is mounted under by a reverse proxy that keeps the prefix
//                (e.g. /blackbox-viewer). Requests outside of it get a 404.
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { leaderboardMiddleware } from './leaderboard.mjs';

const PORT = Number(process.env.PORT ?? 8787);
const HOST = process.env.HOST || undefined;
const BASE_PATH = (process.env.BASE_PATH ?? '').trim().replace(/\/+$/, '');
const DIST = resolve(fileURLToPath(new URL('../dist/', import.meta.url)));
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.wasm': 'application/wasm',
  '.woff2': 'font/woff2',
};

const api = leaderboardMiddleware();

async function serveStatic(req, res) {
  const pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://local').pathname);
  let file = normalize(join(DIST, pathname));
  if (file !== DIST && !file.startsWith(DIST + sep)) {
    res.statusCode = 403;
    return res.end('Forbidden');
  }
  try {
    if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
  } catch {
    file = join(DIST, 'index.html'); // SPA fallback
  }
  try {
    const body = await readFile(file);
    res.setHeader('Content-Type', TYPES[extname(file)] ?? 'application/octet-stream');
    res.end(body);
  } catch {
    res.statusCode = 404;
    res.end('Not found – erst "npm run build" ausführen.');
  }
}

// Strips BASE_PATH from the request URL. Returns false if the request is outside of it
// (the response has then already been sent).
function stripBasePath(req, res) {
  if (!BASE_PATH) return true;
  const url = new URL(req.url ?? '/', 'http://local');
  if (url.pathname === BASE_PATH) {
    // Without the trailing slash the relative asset paths would resolve one level too high.
    res.statusCode = 308;
    res.setHeader('Location', `${BASE_PATH}/${url.search}`);
    res.end();
    return false;
  }
  if (!url.pathname.startsWith(BASE_PATH + '/')) {
    res.statusCode = 404;
    res.end('Not found');
    return false;
  }
  req.url = url.pathname.slice(BASE_PATH.length) + url.search;
  return true;
}

createServer((req, res) => {
  if (!stripBasePath(req, res)) return;
  api(req, res, () => serveStatic(req, res));
}).listen(PORT, HOST, () =>
  console.log(`Blackbox Arena läuft auf http://${HOST ?? 'localhost'}:${PORT}${BASE_PATH}/`),
);
