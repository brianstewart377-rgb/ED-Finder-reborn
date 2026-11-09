import type { CatalogueViewportRequest } from '$lib/api/client';
import type { CameraState } from './contracts';
import {
  normalizeGalaxyCamera,
  type GalaxyCameraViewport,
} from './galaxy-camera';
import { galaxyDetailMode } from './galaxy-detail-policy';
import {
  GALAXY_CATALOGUE_BOUNDS,
  galaxyVisibleFootprint,
} from './galaxy-star-footprint';

export const COMMANDER_HISTORY_POINT_LIMIT = 20_000;
// The journal service validates each coordinate within +/-100,000 LY. Include
// its full vertical envelope at overview instead of the catalogue's thin band.
export const COMMANDER_HISTORY_GALAXY_BOUNDS = {
  ...GALAXY_CATALOGUE_BOUNDS,
  minY: -100_000,
  maxY: 100_000,
} as const;

/**
 * Personal travel owns its visible-area request. Overview covers the galaxy;
 * closer views follow the camera with 15% overscan, without the exact-star
 * 15,000 LY window. Callers supply the same debounced camera used for streaming.
 */
export function commanderHistoryViewport(
  camera: CameraState | null,
  viewport: GalaxyCameraViewport | null,
): CatalogueViewportRequest | null {
  if (!camera || !viewport) return null;
  const state = normalizeGalaxyCamera(camera);
  if (galaxyDetailMode(state.distanceLy) === 'wide') {
    return {
      ...COMMANDER_HISTORY_GALAXY_BOUNDS,
      limit: COMMANDER_HISTORY_POINT_LIMIT,
    };
  }
  const verticalRadius = Math.max(24, state.distanceLy * 0.34);
  const clampY = (y: number) =>
    Math.max(
      COMMANDER_HISTORY_GALAXY_BOUNDS.minY,
      Math.min(COMMANDER_HISTORY_GALAXY_BOUNDS.maxY, y),
    );
  const minY = clampY(state.focusLy.y - verticalRadius);
  const maxY = clampY(state.focusLy.y + verticalRadius);
  const footprint = galaxyVisibleFootprint(state, viewport, minY, maxY);
  return {
    minX: footprint.minX,
    maxX: footprint.maxX,
    minY,
    maxY,
    minZ: footprint.minZ,
    maxZ: footprint.maxZ,
    limit: COMMANDER_HISTORY_POINT_LIMIT,
  };
}
