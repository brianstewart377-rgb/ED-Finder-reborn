// The small server's rules, apart from the server itself so they can be tested:
// which file a request means, and what kind of file it is.
import { extname, join, normalize, resolve, sep } from 'node:path';

export const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.wasm': 'application/wasm',
  '.bin': 'application/octet-stream',
};

/**
 * The file a request path means inside `root`, or null when the path has a
 * ".." step or would leave the folder. A folder means its index.html.
 */
export function fileFor(root, requestPath) {
  let path;
  try {
    path = decodeURIComponent(String(requestPath).split('?')[0].split('#')[0]);
  } catch {
    return null;
  }
  if (path.includes('\0')) return null;
  // Any ".." step is refused outright, whichever way the slashes lean.
  if (path.split(/[\\/]/u).includes('..')) return null;
  if (path.endsWith('/')) path += 'index.html';
  const base = resolve(root);
  const file = resolve(join(base, normalize(path)));
  return file === base || file.startsWith(base + sep) ? file : null;
}

export function typeOf(file) {
  return TYPES[extname(file).toLowerCase()] ?? 'application/octet-stream';
}
