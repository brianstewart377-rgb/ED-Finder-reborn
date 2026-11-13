// Builds the trial page into ./site as plain files that any static server can
// hand out. "base" is relative so the page works from any folder.
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { copyWorkerFiles } from './worker-files.mjs';

const here = (path) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  base: './',
  build: { outDir: 'site', emptyOutDir: true, target: 'es2022', chunkSizeWarningLimit: 4000 },
  plugins: [
    {
      name: 'copy-what-the-map-workers-ask-for',
      apply: 'build',
      async closeBundle() {
        const copied = await copyWorkerFiles(
          here('./site/assets'),
          here('./node_modules/@elite-dangerous-almanac/galaxy-map/dist/assets'),
        );
        console.log(`Copied for the map's workers: ${copied.join(', ') || 'nothing'}`);
      },
    },
  ],
});
