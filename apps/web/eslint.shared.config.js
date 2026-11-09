import tseslint from 'typescript-eslint';

const generatedFiles = ['packages/api-client/src/generated/**/*.ts'];

export default tseslint.config(
  ...tseslint.configs.recommended.map((config) => ({
    ...config,
    ignores: [...(config.ignores ?? []), ...generatedFiles],
  })),
  {
    files: ['packages/**/*.ts'],
    ignores: generatedFiles,
    rules: {
      '@typescript-eslint/ban-ts-comment': [
        'warn',
        {
          'ts-expect-error': 'allow-with-description',
          'ts-ignore': 'allow-with-description',
          'ts-nocheck': 'allow-with-description',
          'ts-check': false,
          minimumDescriptionLength: 3,
        },
      ],
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
        },
      ],
    },
  },
);
