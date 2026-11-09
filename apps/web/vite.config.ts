import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

import { authoritativeGalaxyRegionsPlugin } from './vite.galaxy-regions.ts';

const apiTarget = process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8002';
const backendProxy = {
  '^/api(?:$|[/?])': apiTarget,
  '^/openapi\\.json(?:\\?.*)?$': apiTarget,
  '^/s/\\d+(?:\\?.*)?$': apiTarget,
};

export default defineConfig({
  plugins: [authoritativeGalaxyRegionsPlugin(), tailwindcss(), sveltekit()],
  resolve: { conditions: ['browser'] },
  server: { proxy: backendProxy },
  preview: { proxy: backendProxy },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules/@babylonjs/core')) return undefined;
          if (id.includes('/Materials/')) return 'babylon-materials';
          if (id.includes('/Meshes/')) return 'babylon-meshes';
          if (id.includes('/Engines/')) return 'babylon-engines';
          if (id.includes('/Maths/')) return 'babylon-maths';
          if (id.includes('/Cameras/')) return 'babylon-cameras';
          if (id.includes('/Lights/')) return 'babylon-lights';
          if (id.includes('/Layers/')) return 'babylon-layers';
          if (id.includes('/Culling/')) return 'babylon-culling';
          if (id.includes('/Buffers/')) return 'babylon-buffers';
          if (id.includes('/Rendering/')) return 'babylon-rendering';
          if (id.includes('/Shaders')) return 'babylon-shaders';
          if (id.includes('/Misc/')) return 'babylon-misc';
          return 'babylon-core';
        },
      },
    },
  },

  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'scripts/**/*.test.ts'],
    setupFiles: ['./vitest-setup.ts'],
  },
});
