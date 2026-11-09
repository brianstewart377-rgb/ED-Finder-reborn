import { describe, expect, it } from 'vitest';

import { catalogueDensitySceneLayer } from './galaxy-density';
import {
  buildGalaxyReviewFixtureScene,
  galaxyReviewDensity,
  galaxyReviewDensityRefreshed,
  galaxyReviewSystems,
} from './galaxy-review-fixture';

describe('Galaxy Review Lab fixture', () => {
  it('is deterministic, explicitly synthetic and lossless', () => {
    const first = buildGalaxyReviewFixtureScene(4);
    const second = buildGalaxyReviewFixtureScene(4);
    const density = catalogueDensitySceneLayer(first)?.payload;

    expect(first).toEqual(second);
    expect(density?.generationId).toMatch(/^fixture:/);
    expect(density?.complete).toBe(true);
    expect(density?.coveredSystemCount).toBe(
      galaxyReviewDensity.sourceSystemCount,
    );
    expect(first.selection).toEqual([
      { kind: 'system', systemId64: '9007199254740994' },
    ]);
    expect(
      galaxyReviewSystems.every(
        (system) => typeof system.systemId64 === 'string',
      ),
    ).toBe(true);
  });

  it('provides a later reconciled generation for live refresh diagnostics', () => {
    expect(galaxyReviewDensityRefreshed.generationId).not.toBe(
      galaxyReviewDensity.generationId,
    );
    expect(galaxyReviewDensityRefreshed.sourceSystemCount).toBe(
      galaxyReviewDensity.sourceSystemCount + 6,
    );
    expect(galaxyReviewDensityRefreshed.coveredSystemCount).toBe(
      galaxyReviewDensityRefreshed.sourceSystemCount,
    );
    expect(galaxyReviewDensityRefreshed.complete).toBe(true);
  });
});
