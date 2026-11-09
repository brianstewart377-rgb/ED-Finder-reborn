import { defineConfig } from '@hey-api/openapi-ts';

const requestedInput = process.env.OPENAPI_INPUT?.trim();

if (!requestedInput) {
  throw new Error(
    'OPENAPI_INPUT is required and must point to an authoritative FastAPI OpenAPI URL or file',
  );
}

export default defineConfig({
  input: './.svelte-kit/openapi.json',
  output: './src/lib/api/generated',
  plugins: [
    {
      baseUrl: false,
      name: '@hey-api/client-fetch',
    },
    '@hey-api/sdk',
    '@hey-api/typescript',
  ],
});
