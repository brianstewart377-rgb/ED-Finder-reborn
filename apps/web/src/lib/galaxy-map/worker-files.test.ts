// @vitest-environment node
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  filesAskedFor,
  GALAXY_MAP_LIBRARY_FILES,
  workerFilesToAdd,
} from '../../../vite.galaxy-map-files.ts';

// The map library's own workers, as installed (version 0.7.1): the build
// copies these into the site without looking inside them.
const WORKERS = readdirSync(GALAXY_MAP_LIBRARY_FILES).filter((name) =>
  /\.worker-.*\.js$/u.test(name),
);
const asBuilt = (name: string) => ({
  type: 'asset' as const,
  fileName: `_app/immutable/assets/${name.replace(/\.js$/u, '.Ab12Cd34.js')}`,
  source: readFileSync(path.join(GALAXY_MAP_LIBRARY_FILES, name)),
});
const built = () =>
  Object.fromEntries(WORKERS.map(asBuilt).map((file) => [file.fileName, file]));
const THE_MAP = {
  type: 'chunk' as const,
  fileName: '_app/immutable/chunks/index.js',
  moduleIds: [
    '/repo/node_modules/@elite-dangerous-almanac/galaxy-map/dist/index.js',
  ],
};

describe('the files the galaxy map’s workers ask for', () => {
  it('finds the galaxy’s detail picture in the library’s real point-cloud worker', () => {
    expect(WORKERS).toHaveLength(3);
    const pointCloud = WORKERS.find((name) => name.startsWith('point-cloud'))!;
    const asked = filesAskedFor(
      readFileSync(path.join(GALAXY_MAP_LIBRARY_FILES, pointCloud), 'utf8'),
    );
    expect(asked).toHaveLength(1);
    expect(asked[0]).toMatch(/^galaxy-detail-.+\.png$/u);
    expect(existsSync(path.join(GALAXY_MAP_LIBRARY_FILES, asked[0]))).toBe(
      true,
    );
  });

  it('takes plain file names only: nothing above or below the worker’s own folder, and nothing worked out as it runs', () => {
    expect(
      filesAskedFor(
        [
          'new URL("beside.png", import.meta.url)',
          "new URL( 'spaced.bin' , import.meta.url )",
          'new URL("beside.png", import.meta.url)',
          'new URL("../above.png", import.meta.url)',
          'new URL("below/it.png", import.meta.url)',
          'new URL("./dotted.png", import.meta.url)',
          'new URL(`made-${up}.png`, import.meta.url)',
          'new URL("elsewhere.png", "https://example.test/")',
        ].join(';'),
      ),
    ).toEqual(['beside.png', 'spaced.bin']);
  });

  it('puts each file beside the worker that asks for it, from the library’s own copy', () => {
    const toAdd = workerFilesToAdd({ ...built(), [THE_MAP.fileName]: THE_MAP });
    expect(toAdd).toHaveLength(1);
    expect(toAdd[0].fileName).toMatch(
      /^_app\/immutable\/assets\/galaxy-detail-.+\.png$/u,
    );
    expect(path.dirname(toAdd[0].from)).toBe(GALAXY_MAP_LIBRARY_FILES);
    // A PNG: the first eight bytes of the file say so.
    expect(readFileSync(toAdd[0].from).subarray(0, 8).toString('hex')).toBe(
      '89504e470d0a1a0a',
    );
  });

  it('adds nothing the build already holds, and nothing for a build without the map', () => {
    const [wanted] = workerFilesToAdd(built());
    expect(
      workerFilesToAdd({
        ...built(),
        [wanted.fileName]: {
          type: 'asset',
          fileName: wanted.fileName,
          source: '',
        },
      }),
    ).toEqual([]);
    expect(
      workerFilesToAdd({
        'server/index.js': {
          type: 'chunk',
          fileName: 'server/index.js',
          moduleIds: ['/repo/src/routes/+page.svelte'],
        },
      }),
    ).toEqual([]);
  });

  it('stops the build when a worker asks for a file the library does not hold', () => {
    expect(() =>
      workerFilesToAdd({
        'assets/volume.worker-abc.js': {
          type: 'asset',
          fileName: 'assets/volume.worker-abc.js',
          source: 'fetch(new URL("gone.bin", import.meta.url))',
        },
      }),
    ).toThrow(
      'assets/volume.worker-abc.js asks for gone.bin, which the map library does not hold',
    );
  });

  it('stops the build when the map is in it and its workers are not', () => {
    expect(() => workerFilesToAdd({ [THE_MAP.fileName]: THE_MAP })).toThrow(
      /none of its workers are/u,
    );
  });
});
