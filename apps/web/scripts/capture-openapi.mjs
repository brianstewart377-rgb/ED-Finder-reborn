import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, URL } from 'node:url';

const source = process.env.OPENAPI_INPUT?.trim();
const fetch = globalThis.fetch;

if (!source) {
  throw new Error(
    'OPENAPI_INPUT is required and must point to an authoritative FastAPI OpenAPI URL or file',
  );
}

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const output = path.join(webRoot, '.svelte-kit', 'openapi.json');

async function readOpenApiSource(input) {
  if (/^https?:\/\//i.test(input)) {
    const response = await fetch(input, {
      headers: { accept: 'application/json' },
    });

    if (!response.ok) {
      throw new Error(
        `OPENAPI_INPUT returned HTTP ${response.status} ${response.statusText}`,
      );
    }

    return response.text();
  }

  const inputPath = input.startsWith('file:')
    ? fileURLToPath(input)
    : path.resolve(process.cwd(), input);
  return readFile(inputPath, 'utf8');
}

const rawSchema = await readOpenApiSource(source);
let schema;

try {
  schema = JSON.parse(rawSchema);
} catch (error) {
  throw new Error('OPENAPI_INPUT did not contain valid JSON', { cause: error });
}

if (
  schema === null ||
  typeof schema !== 'object' ||
  typeof schema.openapi !== 'string' ||
  schema.paths === null ||
  typeof schema.paths !== 'object' ||
  Array.isArray(schema.paths)
) {
  throw new Error(
    'OPENAPI_INPUT did not contain an OpenAPI document with an object-valued paths field',
  );
}

await mkdir(path.dirname(output), { recursive: true });
await writeFile(
  output,
  rawSchema.endsWith('\n') ? rawSchema : `${rawSchema}\n`,
  'utf8',
);
process.stdout.write(
  'Captured authoritative OpenAPI schema for deterministic generation.\n',
);
