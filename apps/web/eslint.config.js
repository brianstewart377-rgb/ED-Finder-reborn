import svelte from 'eslint-plugin-svelte';
import tseslint from 'typescript-eslint';

import svelteConfig from './svelte.config.js';

export default tseslint.config(
  {
    ignores: [
      '.svelte-kit/',
      'build/',
      'cypress/artifacts/',
      'src/lib/api/generated/',
    ],
  },
  ...tseslint.configs.recommended,
  ...svelte.configs.recommended,
  ...svelte.configs.prettier,
  {
    files: ['**/*.svelte', '**/*.svelte.js', '**/*.svelte.ts'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        svelteConfig,
      },
    },
  },
);
