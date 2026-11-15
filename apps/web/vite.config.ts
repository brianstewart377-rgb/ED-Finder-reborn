import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

import { galaxyMapWorkerFilesPlugin } from './vite.galaxy-map-files.ts';

const apiTarget = process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8002';
const backendProxy = {
  '^/api(?:$|[/?])': apiTarget,
  '^/openapi\\.json(?:\\?.*)?$': apiTarget,
  '^/s/\\d+(?:\\?.*)?$': apiTarget,
};

export default defineConfig({
  plugins: [galaxyMapWorkerFilesPlugin(), tailwindcss(), sveltekit()],
  resolve: { conditions: ['browser'] },
  server: { proxy: backendProxy },
  preview: { proxy: backendProxy },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'scripts/**/*.test.ts'],
    setupFiles: ['./vitest-setup.ts'],
  },
});
