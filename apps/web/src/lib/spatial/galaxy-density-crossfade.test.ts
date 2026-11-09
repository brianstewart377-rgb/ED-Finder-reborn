import { describe, expect, it } from 'vitest';

import { densityCrossfadeT } from './galaxy-density-crossfade';
import {
  DENSITY_CROSSFADE_NEAR_LY,
  DENSITY_CROSSFADE_FAR_LY,
} from './galaxy-detail-policy';

describe('galaxy density cross-fade band + ramp', () => {
  it('ramps density in as the camera zooms out', () => {
    expect(densityCrossfadeT(DENSITY_CROSSFADE_NEAR_LY - 1)).toBe(0); // zoomed in -> density off
    expect(
      densityCrossfadeT(
        (DENSITY_CROSSFADE_NEAR_LY + DENSITY_CROSSFADE_FAR_LY) / 2,
      ),
    ).toBeCloseTo(0.5, 5);
    expect(densityCrossfadeT(DENSITY_CROSSFADE_FAR_LY + 1)).toBe(1); // zoomed out -> density full
  });

  it('clamps at the exact band edges', () => {
    expect(densityCrossfadeT(DENSITY_CROSSFADE_NEAR_LY)).toBe(0);
    expect(densityCrossfadeT(DENSITY_CROSSFADE_FAR_LY)).toBe(1);
  });

  it('accepts a custom band', () => {
    expect(densityCrossfadeT(50, 0, 100)).toBeCloseTo(0.5, 5);
    expect(densityCrossfadeT(-10, 0, 100)).toBe(0);
    expect(densityCrossfadeT(200, 0, 100)).toBe(1);
  });

  it('degenerates safely when the band has no width', () => {
    expect(densityCrossfadeT(5, 10, 10)).toBe(0);
    expect(densityCrossfadeT(10, 10, 10)).toBe(1);
    expect(densityCrossfadeT(15, 10, 10)).toBe(1);
  });
});
