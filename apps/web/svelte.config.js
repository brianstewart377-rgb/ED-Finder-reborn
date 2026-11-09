import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

const buildSha = process.env.VITE_BUILD_SHA;
if (buildSha !== undefined && !/^[0-9a-f]{40}$/.test(buildSha)) {
  throw new Error(
    'VITE_BUILD_SHA must be exactly 40 lowercase hexadecimal characters',
  );
}

export default {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({ fallback: '200.html' }),
    // SvelteKit otherwise defaults this file-content identity to Date.now(),
    // making two builds of the same reviewed source produce different output.
    version: { name: buildSha ?? 'development' },
  },
};
