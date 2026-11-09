import type { GalaxyNebula, GalaxyNebulaePayload } from './galaxy-nebulae';

export const NEBULA_LANDMARK_CELL_LY = 1_400;
export const NEBULA_LANDMARK_LIMIT = 260;
export const NEBULA_LANDMARK_FADE_START_LY = 8_000;
export const NEBULA_LANDMARK_FADE_END_LY = 2_000;
export const NEBULA_CLOUD_REENTER_LY = 2_400;
export const NEBULA_LANDMARK_MARKER_RADIUS_LY = 3;
// No authorised source supplies nebula extents: these 600–1,800 LY radii
// bound decorative cloud presentation only, never measured physical sizes.
export const NEBULA_SCHEMATIC_RADIUS_MIN_LY = 600;
export const NEBULA_SCHEMATIC_RADIUS_MAX_LY = 1_800;
export const PLANETARY_NEBULA_FADE_START_LY = 4_000;
export const PLANETARY_NEBULA_FADE_END_LY = 3_000;
export const PLANETARY_NEBULA_MARKER_RADIUS_LY = 3;

export function nebulaCloudRadiusLy(identityHash = 0.5): number {
  const fraction = Number.isFinite(identityHash)
    ? Math.max(0, Math.min(1, identityHash))
    : 0.5;
  return (
    NEBULA_SCHEMATIC_RADIUS_MIN_LY +
    fraction * (NEBULA_SCHEMATIC_RADIUS_MAX_LY - NEBULA_SCHEMATIC_RADIUS_MIN_LY)
  );
}

/** Only decorative clouds use this hysteresis; catalogue markers persist. */
export function nebulaCloudEnabled(
  distanceLy: number,
  wasEnabled: boolean,
): boolean {
  if (!Number.isFinite(distanceLy)) return false;
  return wasEnabled
    ? distanceLy > NEBULA_LANDMARK_FADE_END_LY
    : distanceLy >= NEBULA_CLOUD_REENTER_LY;
}

export type NebulaKind = GalaxyNebula['kind'];

export type GalaxyNebulaKindCounts = Readonly<{
  catalogueRecordCount: number;
  acceptedFeatureCount: number;
  selectedLandmarkCount: number;
  deduplicatedEntryCount: number;
  cappedEntryCount: number;
}>;

export type GalaxyNebulaLandmarks = Readonly<{
  landmarks: Record<NebulaKind, readonly GalaxyNebula[]>;
  counts: Record<NebulaKind, GalaxyNebulaKindCounts>;
}>;

function compareIdentities(left: GalaxyNebula, right: GalaxyNebula): number {
  // Codepoint ordering stays the same across browser locales.
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
}

function compareSignificance(left: GalaxyNebula, right: GalaxyNebula): number {
  return (
    Number(right.name !== right.systemName) -
      Number(left.name !== left.systemName) ||
    Number(right.regionName !== null) - Number(left.regionName !== null) ||
    compareIdentities(left, right)
  );
}

function landmarkCell(nebula: GalaxyNebula): string {
  const { x, y, z } = nebula.positionLy.value;
  return `${Math.round(x / NEBULA_LANDMARK_CELL_LY)}:${Math.round(y / NEBULA_LANDMARK_CELL_LY)}:${Math.round(z / NEBULA_LANDMARK_CELL_LY)}`;
}

export function selectGalaxyNebulaLandmarks(
  payload: GalaxyNebulaePayload,
): GalaxyNebulaLandmarks {
  const diffuse = payload.nebulae
    .filter((nebula) => nebula.kind === 'nebula')
    .sort(compareSignificance);
  const planetary = payload.nebulae
    .filter((nebula) => nebula.kind === 'planetary-nebula')
    .sort(compareIdentities);
  const cells = new Set<string>();
  const representatives: GalaxyNebula[] = [];
  for (const nebula of diffuse) {
    const cell = landmarkCell(nebula);
    if (cells.has(cell)) continue;
    cells.add(cell);
    representatives.push(nebula);
  }
  const landmarks = representatives.slice(0, NEBULA_LANDMARK_LIMIT);

  // Parsing is all-or-nothing: every catalogue record in a payload was accepted.
  // Deduplication and the overview cap affect representation, not inventory.
  return {
    landmarks: { nebula: landmarks, 'planetary-nebula': planetary },
    counts: {
      nebula: {
        catalogueRecordCount: diffuse.length,
        acceptedFeatureCount: diffuse.length,
        selectedLandmarkCount: landmarks.length,
        deduplicatedEntryCount: diffuse.length - representatives.length,
        cappedEntryCount: representatives.length - landmarks.length,
      },
      'planetary-nebula': {
        catalogueRecordCount: planetary.length,
        acceptedFeatureCount: planetary.length,
        selectedLandmarkCount: planetary.length,
        deduplicatedEntryCount: 0,
        cappedEntryCount: 0,
      },
    },
  };
}

export function galaxyNebulaVisibility(
  distanceLy: number,
  output: Record<NebulaKind, number> = { nebula: 0, 'planetary-nebula': 0 },
): Record<NebulaKind, number> {
  // `nebula` is cloud opacity, not diffuse landmark visibility. Markers are
  // always enabled; the planetary-nebula class retains its separate fade.
  // The renderer supplies a reusable output to avoid camera-frame allocations.
  output.nebula = Math.max(
    0,
    Math.min(
      1,
      (distanceLy - NEBULA_LANDMARK_FADE_END_LY) /
        (NEBULA_LANDMARK_FADE_START_LY - NEBULA_LANDMARK_FADE_END_LY),
    ),
  );
  output['planetary-nebula'] = Math.max(
    0,
    Math.min(
      1,
      (PLANETARY_NEBULA_FADE_START_LY - distanceLy) /
        (PLANETARY_NEBULA_FADE_START_LY - PLANETARY_NEBULA_FADE_END_LY),
    ),
  );
  return output;
}
