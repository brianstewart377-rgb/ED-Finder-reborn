import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import type { Plugin } from 'vite';

import {
  buildGalaxyRegionsPayload,
  GALAXY_REGION_ASSET_PATH,
  GALAXY_REGION_SOURCE_SHA256,
} from './src/lib/spatial/galaxy-regions.ts';

const webRoot = process.cwd();
const defaultSourcePath = path.resolve(
  webRoot,
  '../importer/src/data/region_map.json',
);
const defaultProvenancePath = path.resolve(
  webRoot,
  '../../assets/PROVENANCE.json',
);

function canonicalLf(value: string): string {
  return value.replace(/\r\n?/g, '\n');
}

export function buildAuthoritativeGalaxyRegionsAsset(
  sourcePath = defaultSourcePath,
  provenancePath = defaultProvenancePath,
): string {
  const sourceText = canonicalLf(readFileSync(sourcePath, 'utf8'));
  const sourceHash = createHash('sha256').update(sourceText).digest('hex');
  if (sourceHash !== GALAXY_REGION_SOURCE_SHA256) {
    throw new Error('Canonical Galaxy region source hash is not reviewed');
  }
  const provenance = JSON.parse(readFileSync(provenancePath, 'utf8')) as {
    entries?: Array<{ filename?: unknown; sha256?: unknown }>;
  };
  const receipt = provenance.entries?.find(
    (entry) => entry.filename === 'apps/importer/src/data/region_map.json',
  );
  if (receipt?.sha256 !== sourceHash) {
    throw new Error(
      'Galaxy region provenance receipt does not match the source',
    );
  }
  return JSON.stringify(
    buildGalaxyRegionsPayload(JSON.parse(sourceText) as unknown, sourceHash),
  );
}

export function authoritativeGalaxyRegionsPlugin(): Plugin {
  let cachedBody: string | undefined;
  const body = () => (cachedBody ??= buildAuthoritativeGalaxyRegionsAsset());
  return {
    name: 'ed-finder-authoritative-galaxy-regions',
    configureServer(server) {
      server.middlewares.use(
        `/${GALAXY_REGION_ASSET_PATH}`,
        (_request, response) => {
          response.statusCode = 200;
          response.setHeader('Content-Type', 'application/json');
          response.setHeader('Cache-Control', 'no-store');
          response.end(body());
        },
      );
    },
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: GALAXY_REGION_ASSET_PATH,
        source: body(),
      });
    },
  };
}
