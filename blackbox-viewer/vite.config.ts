import { defineConfig } from 'vite';
// @ts-ignore – plain ESM module shared with the production server
import { leaderboardMiddleware } from './server/leaderboard.mjs';

export default defineConfig({
  // Relative asset paths: the build works at the domain root and under a sub-path (e.g. /blackbox-viewer/).
  base: './',
  plugins: [
    {
      name: 'leaderboard-api',
      configureServer(server) {
        server.middlewares.use(leaderboardMiddleware());
      },
      configurePreviewServer(server) {
        server.middlewares.use(leaderboardMiddleware());
      },
    },
  ],
  worker: { format: 'es' },
  // two pages: single viewer (index.html) and VS viewer (vs.html) share src/core
  build: { rollupOptions: { input: { main: 'index.html', vs: 'vs.html' } } },
});
