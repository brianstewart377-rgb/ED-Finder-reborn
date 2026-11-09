/**
 * Renderer-neutral semantic-zoom cross-fade policy. Zoomed all the way in
 * (near the catalogue-star band), the density presentation is fully
 * transparent and real stars carry the view; zoomed far out, density carries
 * the view and individual star markers fade away. `densityCrossfadeT` is the
 * single ramp both the density layer and the star layer derive their alpha
 * from, so the two presentations never fight for the same visual budget.
 */

import {
  DENSITY_CROSSFADE_NEAR_LY,
  DENSITY_CROSSFADE_FAR_LY,
} from './galaxy-detail-policy';

export {
  DENSITY_CROSSFADE_NEAR_LY,
  DENSITY_CROSSFADE_FAR_LY,
} from './galaxy-detail-policy';

/**
 * Fraction of the density presentation to show at `distanceLy`: `0` at or
 * below `nearLy` (star-dominant), ramping linearly to `1` at or above `farLy`
 * (density-dominant). Degenerates to a hard step at `nearLy` when the band
 * has no width.
 */
export function densityCrossfadeT(
  distanceLy: number,
  nearLy: number = DENSITY_CROSSFADE_NEAR_LY,
  farLy: number = DENSITY_CROSSFADE_FAR_LY,
): number {
  if (farLy <= nearLy) return distanceLy >= farLy ? 1 : 0;
  return Math.min(1, Math.max(0, (distanceLy - nearLy) / (farLy - nearLy)));
}
