import { describe, expect, it } from 'vitest';

import { densityCrossfadeT } from './galaxy-density-crossfade';
import {
  DENSITY_CROSSFADE_NEAR_LY,
  galaxyDetailMode,
  LOCAL_STAR_ENTER_LY,
  LOCAL_STAR_EXIT_LY,
  type GalaxyDetailMode,
} from './galaxy-detail-policy';

describe('shared Galaxy detail policy', () => {
  it('always carries density or local stars, with both throughout the overlap', () => {
    for (let distanceLy = 500; distanceLy <= 45_000; distanceLy += 100) {
      for (const previousMode of ['wide', 'local'] as const) {
        const densityVisible = densityCrossfadeT(distanceLy) > 0;
        const localActive =
          galaxyDetailMode(distanceLy, previousMode) === 'local';
        expect(
          densityVisible || localActive,
          `detail coverage at ${distanceLy} LY from ${previousMode}`,
        ).toBe(true);
        if (
          distanceLy > DENSITY_CROSSFADE_NEAR_LY &&
          distanceLy <= LOCAL_STAR_ENTER_LY
        ) {
          expect(densityVisible && localActive).toBe(true);
        }
      }
    }
  });

  it('retains its previous mode while hovering between entry and exit', () => {
    let previousMode: GalaxyDetailMode = 'wide';
    const modes = [9_300, 8_900, 9_200, 9_600, 8_800].map((distanceLy) => {
      previousMode = galaxyDetailMode(distanceLy, previousMode);
      return previousMode;
    });
    expect(modes).toEqual(['wide', 'local', 'local', 'wide', 'local']);
  });

  it('enters at the inner boundary and leaves only beyond the outer boundary', () => {
    expect(galaxyDetailMode(LOCAL_STAR_ENTER_LY, 'wide')).toBe('local');
    expect(galaxyDetailMode(LOCAL_STAR_ENTER_LY + 1, 'wide')).toBe('wide');
    expect(galaxyDetailMode(LOCAL_STAR_EXIT_LY, 'local')).toBe('local');
    expect(galaxyDetailMode(LOCAL_STAR_EXIT_LY + 1, 'local')).toBe('wide');
  });
});
