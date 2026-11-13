// The map library's background workers ask for files that sit beside them
// (one is the galaxy's detail picture). The bundler copies the workers but
// does not look inside them, so the page would get a 404 for those files.
// This finds what the workers ask for and copies it next to them.
import { copyFile, readFile, readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

/** The files a worker's code asks for beside itself. */
export function filesAskedFor(source) {
  const asked = new Set();
  const pattern = /new URL\(\s*(["'`])([^"'`$]+)\1\s*,\s*import\.meta\.url\s*\)/gu;
  for (const match of source.matchAll(pattern)) {
    const name = match[2];
    // Only plain file names: a worker's own folder, nothing above or below it.
    if (!name.includes('/') && !name.includes('\\') && !name.startsWith('.')) asked.add(name);
  }
  return [...asked];
}

async function exists(file) {
  try {
    return (await stat(file)).isFile();
  } catch {
    return false;
  }
}

/**
 * Copy into `builtAssets` every file its workers ask for, from the library's
 * own `libraryAssets`. Returns the names copied. A file the library does not
 * hold stops the build: better than a page that quietly draws less.
 */
export async function copyWorkerFiles(builtAssets, libraryAssets) {
  const copied = [];
  for (const name of await readdir(builtAssets)) {
    if (!/\.worker-.*\.js$/u.test(name)) continue;
    for (const asked of filesAskedFor(await readFile(join(builtAssets, name), 'utf8'))) {
      if (await exists(join(builtAssets, asked))) continue;
      if (!(await exists(join(libraryAssets, asked)))) {
        throw new Error(`${name} asks for ${asked}, which the map library does not hold`);
      }
      await copyFile(join(libraryAssets, asked), join(builtAssets, asked));
      copied.push(asked);
    }
  }
  return copied;
}
