import type { SpatialContribution, Truth, Vec3Ly } from './contracts';
import { fetchStaticAsset } from './spatial-assets.ts';

export const GALAXY_NEBULAE_LAYER_ID = 'galaxy-nebulae';
export const GALAXY_NEBULAE_ASSET_URL =
  '/assets/edastro-mapcharts-nebulae.json';

export type GalaxyNebula = Readonly<{
  id: string;
  source: string;
  sourceId: string;
  name: string;
  systemName: string;
  regionName: string | null;
  regionId: number | null;
  kind: 'nebula' | 'planetary-nebula';
  positionLy: Truth<Vec3Ly>;
  poiUrl: string | null;
}>;

export type GalaxyNebulaePayload = Readonly<{
  datasetId: string;
  sourceUrl: string;
  catalogueUrl: string;
  rightsNotice: string;
  usageBasis: string;
  attribution: string;
  sourceLastModified: string | null;
  sourceByteCount: number;
  sourceSha256: string;
  nebulae: readonly GalaxyNebula[];
}>;

type NebulaAsset = Readonly<{
  schemaVersion: number;
  datasetId: unknown;
  sourceUrl: unknown;
  catalogueUrl: unknown;
  rightsNotice: unknown;
  usageBasis: unknown;
  attribution: unknown;
  sourceLastModified: unknown;
  sourceByteCount: unknown;
  sourceSha256: unknown;
  count: unknown;
  nebulae: unknown;
}>;

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`Nebula asset ${field} must be a non-empty string`);
  }
  return value;
}

function optionalRegionId(value: unknown, nebulaId: string): number | null {
  if (value == null) return null;
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < 1 ||
    value > 42
  ) {
    throw new Error(`Nebula ${nebulaId} has an invalid region ID`);
  }
  return value;
}

function sourceByteCount(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error('Nebula asset sourceByteCount must be a positive integer');
  }
  return value;
}

function sourceSha256(value: unknown): string {
  const hash = requiredString(value, 'sourceSha256');
  if (!/^[a-f0-9]{64}$/u.test(hash)) {
    throw new Error('Nebula asset sourceSha256 must be a lowercase SHA-256');
  }
  return hash;
}

export function parseGalaxyNebulaeAsset(value: unknown): GalaxyNebulaePayload {
  if (!value || typeof value !== 'object') {
    throw new Error('Nebula asset must be an object');
  }
  const asset = value as NebulaAsset;
  if (asset.schemaVersion !== 2) {
    throw new Error('Nebula asset schema version is unsupported');
  }
  if (!Array.isArray(asset.nebulae)) {
    throw new Error('Nebula asset must contain a landmark array');
  }
  if (asset.count !== asset.nebulae.length) {
    throw new Error('Nebula asset count does not match its landmarks');
  }

  const sourceLastModified =
    asset.sourceLastModified === null
      ? null
      : requiredString(asset.sourceLastModified, 'sourceLastModified');
  const observedAt = sourceLastModified ?? undefined;
  const seen = new Set<string>();
  const nebulae = asset.nebulae.map((raw): GalaxyNebula => {
    if (!raw || typeof raw !== 'object') {
      throw new Error('Nebula landmark must be an object');
    }
    const candidate = raw as Record<string, unknown>;
    const id = requiredString(candidate.id, 'nebula.id');
    if (seen.has(id)) throw new Error(`Duplicate nebula identity: ${id}`);
    seen.add(id);
    if (candidate.kind !== 'nebula' && candidate.kind !== 'planetary-nebula') {
      throw new Error(`Nebula ${id} has an unsupported kind`);
    }
    const coordinates = candidate.coordinates;
    if (
      !Array.isArray(coordinates) ||
      coordinates.length !== 3 ||
      !coordinates.every(
        (coordinate) =>
          typeof coordinate === 'number' && Number.isFinite(coordinate),
      )
    ) {
      throw new Error(`Nebula ${id} has invalid catalogue coordinates`);
    }
    return {
      id,
      source: requiredString(candidate.source, 'nebula.source'),
      sourceId: requiredString(candidate.sourceId, 'nebula.sourceId'),
      name: requiredString(candidate.name, 'nebula.name'),
      systemName: requiredString(candidate.systemName, 'nebula.systemName'),
      regionName:
        candidate.regionName == null
          ? null
          : requiredString(candidate.regionName, 'nebula.regionName'),
      regionId: optionalRegionId(candidate.regionId, id),
      kind: candidate.kind,
      positionLy: {
        value: { x: coordinates[0], y: coordinates[1], z: coordinates[2] },
        representation: 'AUTHORITATIVE',
        provenance: [
          {
            source: 'EDAstro Mapcharts / CMDR Orvidius',
            observedAt,
            note: `Source identity ${id}; reference-system coordinate`,
          },
        ],
      },
      poiUrl:
        candidate.poiUrl === null
          ? null
          : requiredString(candidate.poiUrl, 'nebula.poiUrl'),
    };
  });

  return {
    datasetId: requiredString(asset.datasetId, 'datasetId'),
    sourceUrl: requiredString(asset.sourceUrl, 'sourceUrl'),
    catalogueUrl: requiredString(asset.catalogueUrl, 'catalogueUrl'),
    rightsNotice: requiredString(asset.rightsNotice, 'rightsNotice'),
    usageBasis: requiredString(asset.usageBasis, 'usageBasis'),
    attribution: requiredString(asset.attribution, 'attribution'),
    sourceLastModified,
    sourceByteCount: sourceByteCount(asset.sourceByteCount),
    sourceSha256: sourceSha256(asset.sourceSha256),
    nebulae,
  };
}

export async function fetchGalaxyNebulae(
  signal?: AbortSignal,
): Promise<GalaxyNebulaePayload> {
  const response = await fetchStaticAsset(GALAXY_NEBULAE_ASSET_URL, {
    signal,
  });
  return parseGalaxyNebulaeAsset(await response.json());
}

export function createGalaxyNebulaeContribution(
  payload: GalaxyNebulaePayload,
  revision: number,
): SpatialContribution {
  return {
    id: 'catalogue:galaxy-nebulae',
    owner: 'CATALOGUE',
    revision,
    layers: [
      {
        id: GALAXY_NEBULAE_LAYER_ID,
        version: 1,
        representation: 'AMBIENT',
        payload,
        targetCount: payload.nebulae.length,
        truncated: false,
      },
    ],
  };
}
