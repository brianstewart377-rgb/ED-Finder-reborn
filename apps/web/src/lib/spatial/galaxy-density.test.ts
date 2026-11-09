import { describe, expect, it } from 'vitest';

import { buildFixtureCatalogueDensity } from './galaxy-density.fixture';
import {
  CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS,
  catalogueDensityRadiusLy,
  catalogueDensitySceneLayer,
  createCatalogueDensityContribution,
  validateCatalogueDensityPayload,
} from './galaxy-density';
import type { GalaxySceneContract } from './contracts';

const fixtureOptions = {
  generationId: 'fixture:density-truth-v1' as const,
  pyramidVersion: 'fixture-pyramid-v1',
  level: 2,
  cellSizeLy: 100,
  cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
  boundsLy: {
    min: { x: -1_000, y: -1_000, z: -1_000 },
    max: { x: 1_000, y: 1_000, z: 1_000 },
  },
  coverageAsOf: '2026-09-13T00:00:00Z',
};

const points = [
  { positionLy: { x: 12, y: -4, z: 25 } },
  { positionLy: { x: 18, y: -2, z: 28 } },
  { positionLy: { x: 410, y: 90, z: -240 } },
];

describe('catalogue density truth contract', () => {
  it('builds deterministic fixture cells that reconcile to every source point', () => {
    const first = buildFixtureCatalogueDensity(points, fixtureOptions);
    const second = buildFixtureCatalogueDensity(
      [...points].reverse(),
      fixtureOptions,
    );

    expect(first).toEqual(second);
    expect(first.sourceSystemCount).toBe(3);
    expect(first.coveredSystemCount).toBe(3);
    expect(first.cells.map((cell) => cell.systemCount)).toEqual([2, 1]);
  });

  it('rejects complete payloads whose factual counts do not reconcile', () => {
    const payload = buildFixtureCatalogueDensity(points, fixtureOptions);

    expect(() =>
      validateCatalogueDensityPayload({
        ...payload,
        sourceSystemCount: payload.sourceSystemCount + 1,
      }),
    ).toThrow(/reconcile exactly to sourceSystemCount/);
    expect(() =>
      validateCatalogueDensityPayload({
        ...payload,
        coveredSystemCount: payload.coveredSystemCount + 1,
      }),
    ).toThrow(/cell counts must reconcile/);
  });

  it('rejects invented positions outside the declared source cell', () => {
    const payload = buildFixtureCatalogueDensity(points, fixtureOptions);
    const cell = payload.cells[0]!;

    expect(() =>
      validateCatalogueDensityPayload({
        ...payload,
        cells: [{ ...cell, centroidLy: { x: 999, y: 999, z: 999 } }],
        sourceSystemCount: cell.systemCount,
        coveredSystemCount: cell.systemCount,
      }),
    ).toThrow(/inside its declared cell/);
  });

  it('allows complete viewport counts without implying the whole generation is in view', () => {
    const payload = buildFixtureCatalogueDensity(points, fixtureOptions);
    const viewport = validateCatalogueDensityPayload({
      ...payload,
      coverageScope: 'viewport',
      sourceSystemCount: 10,
    });
    expect(viewport.coveredSystemCount).toBe(3);
    expect(viewport.sourceSystemCount).toBe(10);
    expect(viewport.complete).toBe(true);
    expect(() =>
      validateCatalogueDensityPayload({ ...viewport, sourceSystemCount: 2 }),
    ).toThrow(/cannot exceed/);
    expect(() =>
      validateCatalogueDensityPayload({
        ...viewport,
        coverageScope: 'unknown',
      }),
    ).toThrow(/coverageScope/);
  });

  it('requires one catalogue-owned DERIVED layer with honest target state', () => {
    const payload = buildFixtureCatalogueDensity(points, fixtureOptions);
    const contribution = createCatalogueDensityContribution(payload, 11);
    const scene: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 11,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 2_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 11,
      },
      selection: [],
      contributions: [contribution],
    };

    expect(catalogueDensitySceneLayer(scene)).toMatchObject({
      contributionId: 'catalogue-density-base',
      contributionRevision: 11,
      payload: { generationId: 'fixture:density-truth-v1' },
    });
    expect(() =>
      catalogueDensitySceneLayer({
        ...scene,
        contributions: [
          {
            ...contribution,
            layers: [{ ...contribution.layers[0]!, targetCount: 99 }],
          },
        ],
      }),
    ).toThrow(/targetCount/);
  });

  it('uses only system count and cell size for bounded presentation radius', () => {
    const sparse = catalogueDensityRadiusLy(1, 100, 200);
    const dense = catalogueDensityRadiusLy(100, 100, 200);

    // Past the neighbouring cell (one cell away), so cells cannot read as a
    // grid of dots, and never beyond 2 cells.
    expect(sparse).toBeGreaterThanOrEqual(200 * 1.6);
    expect(dense).toBeLessThanOrEqual(
      200 * CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS,
    );
    expect(dense).toBeGreaterThan(sparse);
  });
});
