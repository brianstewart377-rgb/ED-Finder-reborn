import { describe, expect, it } from 'vitest';
import type { ExploreSystem } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import type { SpatialContribution } from './contracts';
import {
  buildExploreGalaxyScene,
  createExploreFinderContribution,
  createExploreSelectionContribution,
  mapExploreSelectedSystemPoint,
} from './explore-scene';

describe('Explore Galaxy scene mapper', () => {
  it('maps canonical light-year coordinates and unsafe id64 strings losslessly', () => {
    const id64 = parseId64('9007199254740993');
    const scene = buildExploreGalaxyScene(
      [
        {
          id64,
          name: 'Lossless Reach',
          coords: { x: 12.5, y: -4, z: 80.25 },
          distance: 15.5,
          population: 1_250_000,
          primaryEconomy: 'High Tech',
          security: 'High',
          allegiance: 'Federation',
          government: 'Democracy',
          main_star_type: 'G',
          main_star_subtype: '2 V',
        },
      ] as ExploreSystem[],
      id64,
      7,
    );

    expect(scene.selection).toEqual([{ kind: 'system', systemId64: id64 }]);
    expect(scene.contributions[0]?.layers[0]?.payload).toEqual({
      systems: [
        {
          systemId64: '9007199254740993',
          name: 'Lossless Reach',
          positionLy: { x: 12.5, y: -4, z: 80.25 },
          primaryStar: { type: 'G', subtype: '2 V' },
          summary: {
            distanceLy: 15.5,
            population: 1_250_000,
            primaryEconomy: 'High Tech',
            security: 'High',
            allegiance: 'Federation',
            government: 'Democracy',
          },
        },
      ],
    });
  });

  it('omits incomplete and non-finite coordinates instead of inventing them', () => {
    const systems = [
      { id64: parseId64('1'), name: 'Unknown', coords: null },
      {
        id64: parseId64('2'),
        name: 'Partial',
        coords: { x: 1, y: null, z: 3 },
      },
      {
        id64: parseId64('3'),
        name: 'Invalid',
        coords: { x: Infinity, y: 2, z: 3 },
      },
    ] as ExploreSystem[];

    const scene = buildExploreGalaxyScene(systems, null, 1);
    expect(scene.contributions[0]?.layers[0]?.targetCount).toBe(0);
    expect(scene.contributions[0]?.layers[0]?.payload).toEqual({ systems: [] });
  });

  it('keeps render inputs stable while composing authoritative regions and selection', () => {
    const systems = [
      {
        id64: parseId64('10477373803000'),
        name: 'Achenar',
        coords: { x: 67.5, y: -119.46875, z: 24.84375 },
      },
    ] as ExploreSystem[];
    const finder = createExploreFinderContribution(systems, 70);
    const regions = {
      id: 'authoritative-galaxy-regions',
      owner: 'SPATIAL_PLATFORM',
      revision: 1,
      layers: [],
    } satisfies SpatialContribution;
    const camera = {
      focusLy: { x: 1, y: 2, z: 3 },
      distanceLy: 500,
      bearingRad: 0.2,
      pitchRad: 0.8,
      projection: 'perspective',
      revision: 12,
    } as const;

    const scene = buildExploreGalaxyScene(systems, systems[0]!.id64, 72, {
      finderContribution: finder,
      finderRevision: 70,
      camera,
      spatialContributions: [regions],
      selectedRegionId: '18',
    });

    expect(scene.camera).toBe(camera);
    expect(scene.contributions).toEqual([finder, regions]);
    expect(scene.contributions[0]).toBe(finder);
    expect(scene.selection).toEqual([
      { kind: 'system', systemId64: '10477373803000' },
      { kind: 'region', id: '18' },
    ]);
  });

  it('retains an omitted selected point separately and deduplicates it when its page returns', () => {
    const selected = {
      id64: parseId64('9007199254740993'),
      name: 'Selected Reach',
      coords: { x: 12, y: -4, z: 80 },
      archetype_score: 88,
      archetype_tier: 'S',
      primary_archetype: 'mining',
    } as ExploreSystem;
    const nextPage = [
      {
        id64: parseId64('42'),
        name: 'Next page',
        coords: { x: 1, y: 2, z: 3 },
      },
    ] as ExploreSystem[];
    const selectedPoint = mapExploreSelectedSystemPoint(selected)!;
    const scene = buildExploreGalaxyScene(nextPage, selected.id64, 2, {
      selectedPoint,
    });
    expect(scene.contributions.map((part) => part.id)).toEqual([
      'finder-results',
      'finder-selection',
    ]);
    expect(scene.contributions[1]?.layers[0]?.payload).toEqual({
      systems: [selectedPoint],
    });
    expect(JSON.stringify(scene)).not.toMatch(/archetype|score|tier/);
    expect(scene.selection).toEqual([
      { kind: 'system', systemId64: selected.id64 },
    ]);
    const selectionContribution = createExploreSelectionContribution(
      selectedPoint,
      nextPage,
      2,
    );
    const selectionOnlyScene = buildExploreGalaxyScene(
      nextPage,
      selected.id64,
      3,
      {
        selectedPoint,
        selectionContribution,
        finderContribution: scene.contributions[0],
        camera: scene.camera,
        selectedRegionId: '18',
      },
    );
    expect(selectionOnlyScene.contributions[1]).toBe(selectionContribution);
    expect(selectionOnlyScene.contributions[0]).toBe(scene.contributions[0]);
    expect(
      buildExploreGalaxyScene([selected], selected.id64, 3, { selectedPoint })
        .contributions,
    ).toHaveLength(1);
    expect(
      buildExploreGalaxyScene(nextPage, nextPage[0]!.id64, 4, { selectedPoint })
        .contributions,
    ).toHaveLength(1);
    expect(
      buildExploreGalaxyScene(nextPage, null, 5, { selectedPoint })
        .contributions,
    ).toHaveLength(1);
  });

  it('validates selected coordinates and accepts the origin only for canonical Sol', () => {
    for (const coords of [
      null,
      { x: NaN, y: 2, z: 3 },
      { x: 1, y: Infinity, z: 3 },
      { x: 0, y: 0, z: 0 },
    ]) {
      const selectedPoint = mapExploreSelectedSystemPoint({
        id64: parseId64('42'),
        name: 'Unknown position',
        coords,
      } as ExploreSystem);
      expect(selectedPoint).toBeNull();
      const scene = buildExploreGalaxyScene([], parseId64('42'), 1, {
        selectedPoint,
      });
      expect(scene.selection).toEqual([{ kind: 'system', systemId64: '42' }]);
      expect(scene.contributions).toHaveLength(1);
      expect(scene.contributions[0]?.layers[0]?.payload).toEqual({
        systems: [],
      });
    }
    const sol = mapExploreSelectedSystemPoint({
      id64: parseId64('10477373803'),
      name: 'Sol',
      coords: { x: 0, y: 0, z: 0 },
    } as ExploreSystem)!;
    expect(
      createExploreSelectionContribution(sol, [], 1)?.layers[0]?.targetCount,
    ).toBe(1);
    expect(
      createExploreSelectionContribution(
        { ...sol, systemId64: parseId64('42') },
        [],
        1,
      ),
    ).toBeNull();
    expect(
      createExploreSelectionContribution(
        { ...sol, positionLy: { x: NaN, y: 0, z: 0 } },
        [],
        1,
      ),
    ).toBeNull();
    expect(createExploreSelectionContribution(null, [], 1)).toBeNull();
  });

  it('does not map unrelated page entries to deduplicate the retained selection', () => {
    const selectedPoint = mapExploreSelectedSystemPoint({
      id64: parseId64('42'),
      name: 'Retained selection',
      coords: { x: 1, y: 2, z: 3 },
    } as ExploreSystem)!;
    const unrelated = {
      id64: parseId64('43'),
      get coords(): never {
        throw new Error('Unrelated page coordinates must not be remapped');
      },
    } as ExploreSystem;
    expect(
      createExploreSelectionContribution(selectedPoint, [unrelated], 1)
        ?.layers[0]?.targetCount,
    ).toBe(1);
    expect(
      createExploreSelectionContribution(
        selectedPoint,
        [
          unrelated,
          { id64: selectedPoint.systemId64, coords: selectedPoint.positionLy },
        ] as ExploreSystem[],
        1,
      ),
    ).toBeNull();
    expect(
      createExploreSelectionContribution(
        selectedPoint,
        [{ id64: selectedPoint.systemId64, coords: null }] as ExploreSystem[],
        1,
      )?.layers[0]?.targetCount,
    ).toBe(1);
  });
});
