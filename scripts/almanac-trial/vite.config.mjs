// Builds the trial page into ./site as plain files that any static server can
// hand out. "base" is relative so the page works from any folder.
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { copyWorkerFiles } from './worker-files.mjs';

const here = (path) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  base: './',
  build: {
    outDir: 'site',
    emptyOutDir: true,
    target: 'es2022',
    chunkSizeWarningLimit: 4000,
    // Plain file names, the same on every build, so a newer build put over an
    // older one replaces its files instead of piling up beside them. The
    // small server tells the browser never to keep a copy, so no name needs
    // to change for a change to be seen.
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
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
