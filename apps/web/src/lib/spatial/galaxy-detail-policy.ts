/**
 * Shared semantic-zoom policy for aggregate density and real catalogue stars.
 * Local stars arrive before density fades out, including when zoom direction
 * changes inside the hysteresis band. All distances are Elite light-years.
 */
export const DENSITY_CROSSFADE_NEAR_LY = 6_000;
export const DENSITY_CROSSFADE_FAR_LY = 40_000;
export const LOCAL_STAR_ENTER_LY = 9_000;
export const LOCAL_STAR_EXIT_LY = 9_500;

export type GalaxyDetailMode = 'wide' | 'local';

/** Enter local detail on zoom-in; retain it until the outer exit is crossed. */
export function galaxyDetailMode(
  distanceLy: number,
  previousMode: GalaxyDetailMode = 'wide',
): GalaxyDetailMode {
  if (previousMode === 'local') {
    return distanceLy > LOCAL_STAR_EXIT_LY ? 'wide' : 'local';
  }
  return distanceLy <= LOCAL_STAR_ENTER_LY ? 'local' : 'wide';
}

export const GALAXY_WIDE_STAR_LIMIT = 8_000;
export const GALAXY_STAR_API_CUBE_BOUND_LY = 15_000;
export const LOCAL_STAR_MIN_RADIUS_LY = 24;
export const LOCAL_STAR_MAX_HORIZONTAL_RADIUS_LY = 7_400;
export const LOCAL_STAR_MAX_VERTICAL_RADIUS_LY = 3_000;

export const LOCAL_STAR_BUDGET_BANDS = [
  { aboveDistanceLy: 3_500, limit: 40_000 },
  { aboveDistanceLy: 1_400, limit: 24_000 },
  { aboveDistanceLy: 450, limit: 12_000 },
  { aboveDistanceLy: 140, limit: 5_000 },
] as const;
export const LOCAL_STAR_CLOSE_LIMIT = 1_800;

export function galaxyLocalStarLimit(distanceLy: number): number {
  return (
    LOCAL_STAR_BUDGET_BANDS.find((band) => distanceLy > band.aboveDistanceLy)
      ?.limit ?? LOCAL_STAR_CLOSE_LIMIT
  );
}
