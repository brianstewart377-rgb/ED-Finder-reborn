/**
 * The galaxy map's background workers ask for files that sit beside them
 * (one is the galaxy's detail picture). The bundler copies the workers into
 * the site as they are and does not look inside them, so the browser would
 * get a 404 for those files and the map would quietly draw less.
 *
 * This finds what each worker asks for and puts it next to the worker, from
 * the map library's own files. (When developing, the workers are served
 * straight from the library's folder, where those files already are.)
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Plugin } from 'vite';

const LIBRARY = '@elite-dangerous-almanac/galaxy-map';
const WORKER = /\.worker-[^/]*\.js$/u;

/** The folder of the map library's own files, as installed. */
export const GALAXY_MAP_LIBRARY_FILES = fileURLToPath(
  new URL(`./node_modules/${LIBRARY}/dist/assets`, import.meta.url),
);

/** The files a worker's code asks for beside itself. */
export function filesAskedFor(source: string): string[] {
  const asked = new Set<string>();
  const pattern =
    /new URL\(\s*(["'`])([^"'`$]+)\1\s*,\s*import\.meta\.url\s*\)/gu;
  for (const match of source.matchAll(pattern)) {
    const name = match[2];
    // Only plain file names: a worker's own folder, nothing above or below it.
    if (!name.includes('/') && !name.includes('\\') && !name.startsWith('.'))
      asked.add(name);
  }
  return [...asked];
}

type BuiltFile =
  | { type: 'asset'; fileName: string; source: string | Uint8Array }
  | { type: 'chunk'; fileName: string; moduleIds: readonly string[] };

/**
 * What must be added to a build: every file its workers ask for that it does
 * not already hold, each beside the worker that asks. A file the library does
 * not hold is an error: better a build that stops than a map that quietly
 * draws less. So is a build that holds the map and none of its workers.
 */
export function workerFilesToAdd(
  built: Readonly<Record<string, BuiltFile>>,
  libraryFiles = GALAXY_MAP_LIBRARY_FILES,
): { fileName: string; from: string }[] {
  const toAdd = new Map<string, string>();
  let workers = 0;
  for (const file of Object.values(built)) {
    if (file.type !== 'asset' || !WORKER.test(file.fileName)) continue;
    workers += 1;
    const source =
      typeof file.source === 'string'
        ? file.source
        : Buffer.from(file.source).toString('utf8');
    const folder = path.posix.dirname(file.fileName);
    for (const asked of filesAskedFor(source)) {
      const fileName = path.posix.join(folder, asked);
      if (fileName in built || toAdd.has(fileName)) continue;
      const from = path.join(libraryFiles, asked);
      if (!existsSync(from))
        throw new Error(
          `${file.fileName} asks for ${asked}, which the map library does not hold`,
        );
      toAdd.set(fileName, from);
    }
  }
  const holdsTheMap = Object.values(built).some(
    (file) =>
      file.type === 'chunk' &&
      file.moduleIds.some((id) => id.includes(`/${LIBRARY}/`)),
  );
  if (holdsTheMap && workers === 0)
    throw new Error(
      'The galaxy map is in this build but none of its workers are, so the files they ask for cannot be found',
    );
  return [...toAdd].map(([fileName, from]) => ({ fileName, from }));
}

export function galaxyMapWorkerFilesPlugin(): Plugin {
  return {
    name: 'ed-finder-galaxy-map-worker-files',
    apply: 'build',
    generateBundle(_options, bundle) {
      // Only the browser's half of the build holds files to hand out.
      if (this.environment.name !== 'client') return;
      for (const { fileName, from } of workerFilesToAdd(
        bundle as unknown as Record<string, BuiltFile>,
      ))
        this.emitFile({
          type: 'asset',
          fileName,
          source: readFileSync(from),
        });
    },
  };
}
