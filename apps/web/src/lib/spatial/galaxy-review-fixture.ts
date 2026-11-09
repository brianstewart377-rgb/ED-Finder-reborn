import type {
  GalaxySceneContract,
  GalaxySystemPoint,
  GalaxySystemsPayload,
} from './contracts';
import { buildFixtureCatalogueDensity } from './galaxy-density.fixture';
import {
  createCatalogueDensityContribution,
  type CatalogueDensityPayload,
} from './galaxy-density';

const fixtureCoordinates = [
  [-720, -38, -460],
  [-680, -22, -420],
  [-650, 12, -390],
  [-620, 30, -360],
  [-590, -12, -330],
  [-430, 20, -260],
  [-390, 8, -230],
  [-350, -16, -210],
  [-180, -8, -80],
  [-150, 5, -55],
  [-120, 18, -30],
  [-90, -14, -5],
  [-60, 2, 20],
  [-30, 12, 45],
  [0, -6, 70],
  [35, 4, 95],
  [70, 15, 120],
  [105, -10, 145],
  [150, 8, 180],
  [190, 24, 210],
  [240, -18, 245],
  [300, -5, 275],
  [360, 12, 315],
  [430, 30, 360],
  [500, 10, 410],
  [570, -20, 470],
  [650, 5, 540],
  [720, 22, 610],
  [-80, 1, 15],
  [-55, 3, 35],
  [-20, -2, 55],
  [20, 7, 78],
  [55, -4, 105],
  [95, 9, 130],
] as const;

const fixturePoints = fixtureCoordinates.map(([x, y, z]) => ({
  positionLy: { x, y, z },
}));

const fixtureRefreshPoints = [
  ...fixturePoints,
  { positionLy: { x: 760, y: -18, z: -520 } },
  { positionLy: { x: 782, y: -4, z: -500 } },
  { positionLy: { x: 798, y: 11, z: -474 } },
  { positionLy: { x: 820, y: 22, z: -452 } },
  { positionLy: { x: 840, y: 7, z: -430 } },
  { positionLy: { x: 860, y: -10, z: -408 } },
] as const;

export const galaxyReviewDensity: CatalogueDensityPayload =
  buildFixtureCatalogueDensity(fixturePoints, {
    generationId: 'fixture:galaxy-review-v1',
    pyramidVersion: 'fixture-pyramid-v1',
    level: 3,
    cellSizeLy: 180,
    cellOriginLy: { x: -1_800, y: -900, z: -1_800 },
    boundsLy: {
      min: { x: -1_800, y: -900, z: -1_800 },
      max: { x: 1_800, y: 900, z: 1_800 },
    },
    coverageAsOf: '2026-09-13T00:00:00Z',
  });

/** A later immutable fixture generation used to prove live density replacement. */
export const galaxyReviewDensityRefreshed: CatalogueDensityPayload =
  buildFixtureCatalogueDensity(fixtureRefreshPoints, {
    generationId: 'fixture:galaxy-review-v2',
    pyramidVersion: 'fixture-pyramid-v1',
    level: 3,
    cellSizeLy: 180,
    cellOriginLy: { x: -1_800, y: -900, z: -1_800 },
    boundsLy: {
      min: { x: -1_800, y: -900, z: -1_800 },
      max: { x: 1_800, y: 900, z: 1_800 },
    },
    coverageAsOf: '2026-09-13T01:00:00Z',
  });

export const galaxyReviewSystems: readonly GalaxySystemPoint[] = [
  {
    systemId64: '9007199254740993',
    name: 'Fixture West Reach',
    positionLy: { x: -680, y: -22, z: -420 },
  },
  {
    systemId64: '9007199254740994',
    name: 'Fixture Core',
    positionLy: { x: 0, y: -6, z: 70 },
  },
  {
    systemId64: '9007199254740995',
    name: 'Fixture East Reach',
    positionLy: { x: 650, y: 5, z: 540 },
  },
];

/**
 * Deterministic, explicitly synthetic Review Lab scene. It exercises the same
 * Babylon product path without claiming production catalogue coverage.
 */
export function buildGalaxyReviewFixtureScene(
  revision = 1,
): GalaxySceneContract {
  const systemsPayload: GalaxySystemsPayload = {
    systems: galaxyReviewSystems,
  };
  return {
    kind: 'galaxy',
    revision,
    camera: {
      focusLy: { x: 0, y: 0, z: 60 },
      distanceLy: 2_300,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision,
    },
    selection: [
      { kind: 'system', systemId64: galaxyReviewSystems[1]!.systemId64 },
    ],
    contributions: [
      createCatalogueDensityContribution(galaxyReviewDensity, revision),
      {
        id: 'fixture-finder-results',
        owner: 'FINDER',
        revision,
        layers: [
          {
            id: 'finder-systems',
            version: 1,
            representation: 'AUTHORITATIVE',
            payload: systemsPayload,
            targetCount: galaxyReviewSystems.length,
            truncated: false,
          },
        ],
      },
    ],
  };
}
