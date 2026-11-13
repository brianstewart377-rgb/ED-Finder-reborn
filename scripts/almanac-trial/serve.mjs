#!/usr/bin/env node
// Hands out the built trial page (./site) on this computer only.
//
//     node serve.mjs            then open http://localhost:5199
//
// Node's own modules only: nothing to install. Stop it with Ctrl+C.
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fileFor, typeOf } from './serve-files.mjs';

const root = join(fileURLToPath(new URL('.', import.meta.url)), 'site');
const port = Number(process.env.PORT ?? 5199);

try {
  await stat(join(root, 'index.html'));
} catch {
  console.error(`There is no built page in ${root} yet.`);
  console.error('Build it first: see README.md in this folder.');
  process.exit(1);
}

const server = createServer(async (request, response) => {
  const file = request.method === 'GET' || request.method === 'HEAD' ? fileFor(root, request.url) : null;
  if (!file) {
    response.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' }).end('Bad request');
    return;
  }
  try {
    const body = await readFile(file);
    response.writeHead(200, { 'content-type': typeOf(file), 'cache-control': 'no-store' });
    response.end(request.method === 'HEAD' ? undefined : body);
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }).end('Not found');
  }
});
server.on('error', (error) => {
  console.error(error.code === 'EADDRINUSE'
    ? `Port ${port} is already in use. Is the trial page already running in another window?`
    : String(error));
  process.exit(1);
});
server.listen(port, '127.0.0.1', () => {
  console.log(`The trial page is at http://localhost:${port}`);
  console.log('Leave this window open while you look. Stop it with Ctrl+C.');
});
