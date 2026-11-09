import { describe, expect, it } from 'vitest';
import type {
  PlanBodiesV1,
  PlanFacilityV1,
  PlanSystemBeltV1,
  PlanSystemBodyV1,
} from '$lib/api';
import {
  buildBodyTree,
  facilityFitsLocation,
  flattenBodyTree,
  nonCurrentLocation,
  resolvePlanLocations,
  shortBodyName,
} from './locations';

function body(
  id: string,
  parent: string | null,
  type = 'planet',
  name = `Test ${id}`,
): PlanSystemBodyV1 {
  return {
    body_id: id,
    name,
    direct_parent_body_id: parent,
    body_type: type,
    is_main_star: type === 'star',
    is_landable: type === 'planet',
    radius_km: 1000,
    surface_gravity_g: null,
    surface_temperature_k: null,
    atmosphere: null,
    terraforming_state: null,
    volcanism: null,
    distance_from_arrival_ls: null,
    has_rings: false,
    geological_signals: 0,
    biological_signals: 0,
    signals_complete: null,
    lifecycle_state: 'ACTIVE',
  };
}

function belt(
  id: string,
  parent = '1',
  name = 'Test A Belt',
): PlanSystemBeltV1 {
  return {
    belt_id: id,
    parent_body_id: parent,
    name,
    ring_type: 'Rocky',
    reserve: 'Pristine',
    mass_mt: null,
    inner_radius_km: null,
    outer_radius_km: null,
    lifecycle_state: 'ACTIVE',
  };
}

function data(
  bodies: PlanSystemBodyV1[],
  belts: PlanSystemBeltV1[] = [],
): PlanBodiesV1 {
  return {
    system_id64: '42',
    generation_id: 'fixture',
    publication_sequence: '1',
    bodies,
    belts,
  };
}

describe('planner body locations', () => {
  it.each(['RETIRED', 'MISSING_CANDIDATE'])(
    'excludes a landable %s planet and identifies its saved location',
    (lifecycle_state) => {
      const fixture = data([{ ...body('3', null), lifecycle_state }]);
      expect(fixture.bodies[0]).toMatchObject({
        body_id: '3',
        is_landable: true,
        has_rings: false,
        geological_signals: 0,
        biological_signals: 0,
      });
      expect(resolvePlanLocations(fixture)).toEqual([]);
      expect(nonCurrentLocation(fixture, '3')).toEqual({
        name: 'Test 3',
        lifecycle_state,
      });
    },
  );

  it('requires the exact ACTIVE lifecycle and ignores current, unknown and Unplaced references', () => {
    const fixture = data([
      body('1', null),
      { ...body('2', null), lifecycle_state: 'active' },
    ]);
    expect(
      resolvePlanLocations(fixture).map(({ body_ref }) => body_ref),
    ).toEqual(['1']);
    expect(nonCurrentLocation(fixture, '1')).toBeNull();
    expect(nonCurrentLocation(fixture, 'missing')).toBeNull();
    expect(nonCurrentLocation(fixture, null)).toBeNull();
  });

  it.each(['RETIRED', 'MISSING_CANDIDATE'])(
    'excludes a %s belt and identifies both saved aliases',
    (lifecycle_state) => {
      const fixture = data(
        [
          {
            ...body('3', null, 'belt cluster', 'Test A Belt Cluster 1'),
            lifecycle_state: 'RETIRED',
          },
          body('4', null, 'belt cluster', 'Test A Belt Cluster 2'),
        ],
        [{ ...belt('10'), lifecycle_state }],
      );
      expect(resolvePlanLocations(fixture)).toEqual([]);
      for (const bodyRef of ['3', '4', 'belt:10'])
        expect(nonCurrentLocation(fixture, bodyRef)).toEqual({
          name: 'Test A Belt',
          lifecycle_state,
        });
      expect(
        nonCurrentLocation(
          data([], [{ ...belt('10'), lifecycle_state }]),
          'belt:10',
        ),
      ).toEqual({ name: 'Test A Belt', lifecycle_state });
    },
  );

  it('uses only current clusters to represent an ACTIVE belt, with a fallback when all clusters are non-current', () => {
    const fixture = data(
      [
        {
          ...body('3', null, 'belt cluster', 'Test A Belt Cluster 1'),
          lifecycle_state: 'RETIRED',
        },
        body('4', null, 'belt cluster', 'Test A Belt Cluster 2'),
      ],
      [belt('10')],
    );
    expect(
      resolvePlanLocations(fixture).map(({ body_ref }) => body_ref),
    ).toEqual(['4']);
    expect(nonCurrentLocation(fixture, '3')).toEqual({
      name: 'Test A Belt Cluster 1',
      lifecycle_state: 'RETIRED',
    });
    expect(nonCurrentLocation(fixture, '4')).toBeNull();
    expect(nonCurrentLocation(fixture, 'belt:10')).toBeNull();
    fixture.bodies[1].lifecycle_state = 'MISSING_CANDIDATE';
    expect(
      resolvePlanLocations(fixture).map(({ body_ref }) => body_ref),
    ).toEqual(['belt:10']);
    expect(nonCurrentLocation(fixture, '4')).toEqual({
      name: 'Test A Belt Cluster 2',
      lifecycle_state: 'MISSING_CANDIDATE',
    });
  });

  it('walks through non-current parents to the nearest current ancestor, or root', () => {
    const fixture = data([
      body('1', null, 'star'),
      { ...body('2', '1'), lifecycle_state: 'RETIRED' },
      { ...body('3', '2'), lifecycle_state: 'MISSING_CANDIDATE' },
      body('4', '3'),
      { ...body('5', null), lifecycle_state: 'RETIRED' },
      body('6', '5'),
    ]);
    expect(
      resolvePlanLocations(fixture).map(({ body_ref, parent_ref }) => ({
        body_ref,
        parent_ref,
      })),
    ).toEqual([
      { body_ref: '1', parent_ref: null },
      { body_ref: '4', parent_ref: '1' },
      { body_ref: '6', parent_ref: null },
    ]);
    expect(nonCurrentLocation(fixture, '4')).toBeNull();
    expect(nonCurrentLocation(fixture, '6')).toBeNull();
    expect(nonCurrentLocation(fixture, '2')).toEqual({
      name: 'Test 2',
      lifecycle_state: 'RETIRED',
    });
    expect(nonCurrentLocation(fixture, '3')).toEqual({
      name: 'Test 3',
      lifecycle_state: 'MISSING_CANDIDATE',
    });
  });

  it('reparents barycentre descendants to the nearest visible ancestor or root', () => {
    const tree = buildBodyTree(
      data([
        body('0', null, 'barycentre'),
        body('1', '0', 'star'),
        body('2', '1', 'barycentre'),
        body('3', '2', 'barycentre'),
        body('4', '3'),
        body('5', '0'),
      ]),
    );
    expect(
      flattenBodyTree(tree).map(({ body_ref, parent_ref, depth }) => ({
        body_ref,
        parent_ref,
        depth,
      })),
    ).toEqual([
      { body_ref: '1', parent_ref: null, depth: 0 },
      { body_ref: '4', parent_ref: '1', depth: 1 },
      { body_ref: '5', parent_ref: null, depth: 0 },
    ]);
  });

  it('resolves each belt to its lowest numeric cluster and retains child order', () => {
    const fixture = data(
      [
        body('1', null, 'star'),
        body('10', '1', 'belt cluster', 'Test A Belt Cluster 10'),
        body('11', '1', 'belt cluster', 'Test A Belt Cluster 2'),
        body('12', '1', 'planet'),
        body('13', '1', 'belt cluster', 'Test B Belt Cluster 1'),
        body('14', '1', 'belt cluster', 'Test B Belt Cluster 4'),
        body('15', '1', 'belt cluster', 'Unmatched Cluster 1'),
      ],
      [belt('a'), belt('b', '1', 'Test B Belt')],
    );
    const tree = buildBodyTree(fixture);
    expect(
      tree[0].children.map(({ body_ref, kind }) => [body_ref, kind]),
    ).toEqual([
      ['11', 'belt'],
      ['12', 'body'],
      ['13', 'belt'],
    ]);
    const locations = resolvePlanLocations(fixture);
    expect(locations.map(({ body_ref }) => body_ref)).toEqual([
      '1',
      '11',
      '12',
      '13',
    ]);
    expect(
      locations.find((location) => location.body_ref === '11'),
    ).toMatchObject({
      parent_ref: '1',
      has_rings: true,
      is_landable: false,
      body: null,
      belt: { belt_id: 'a' },
    });
    expect(
      locations.some((location) => location.name.includes('Cluster')),
    ).toBe(false);
  });

  it('uses belt:<id> when no cluster matches the exact belt name prefix', () => {
    const locations = resolvePlanLocations(
      data(
        [
          body('1', null, 'star'),
          body('2', '1', 'belt_cluster', 'Test A Belt Extra Cluster 1'),
          body('3', '1', 'belt cluster', 'Test A Belt Cluster x'),
        ],
        [belt('88')],
      ),
    );
    expect(locations.map(({ body_ref }) => body_ref)).toEqual(['1', 'belt:88']);
    expect(
      buildBodyTree(data([body('1', null, 'star')], [belt('88')]))[0]
        .children[0].body_ref,
    ).toBe('belt:88');
  });

  it('attaches belts through barycentres and handles missing or cyclic parents', () => {
    const fixture = data(
      [
        body('1', null, 'star'),
        body('2', '1', 'barycentre'),
        body('3', '4'),
        body('4', '3'),
        body('5', 'missing'),
      ],
      [belt('a', '2')],
    );
    const flat = flattenBodyTree(buildBodyTree(fixture));
    expect(flat.map(({ body_ref }) => body_ref).sort()).toEqual([
      '1',
      '3',
      '4',
      '5',
      'belt:a',
    ]);
    expect(flat.find(({ body_ref }) => body_ref === 'belt:a')?.parent_ref).toBe(
      '1',
    );
  });

  it('offers ringed-orbital facilities on belts and excludes surface facilities', () => {
    const locations = resolvePlanLocations(
      data([body('1', null, 'star')], [belt('a')]),
    );
    const ringed = { allowed_location: 'orbital_ringed' } as PlanFacilityV1;
    const surface = { allowed_location: 'surface' } as PlanFacilityV1;
    expect(facilityFitsLocation(ringed, locations[0])).toBe(false);
    expect(facilityFitsLocation(ringed, locations[1])).toBe(true);
    expect(facilityFitsLocation(surface, locations[1])).toBe(false);
    expect(shortBodyName('Test A Belt', 'Test')).toBe('A Belt');
    expect(shortBodyName('Another Test', 'Test')).toBe('Another Test');
  });
});
