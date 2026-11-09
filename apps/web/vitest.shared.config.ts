import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

const repoRoot = fileURLToPath(new URL('../..', import.meta.url));
const apiSource = path.resolve(repoRoot, 'packages/api-client/src');
const require = createRequire(import.meta.url);

export default defineConfig({
  root: repoRoot,
  resolve: {
    alias: [
      {
        find: /^@ed-finder\/api-client\/(.+)$/,
        replacement: `${apiSource}/$1.ts`,
      },
      {
        find: '@ed-finder/api-client',
        replacement: path.resolve(apiSource, 'index.ts'),
      },
      {
        find: 'json-with-bigint',
        replacement: require.resolve('json-with-bigint'),
      },
    ],
  },
  test: {
    environment: 'jsdom',
    include: [
      'packages/api-client/src/**/*.test.ts',
      'packages/planner-core/src/**/*.test.ts',
    ],
  },
});
