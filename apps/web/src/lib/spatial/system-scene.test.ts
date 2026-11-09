import { describe, expect, it } from 'vitest';

import { systemDetailFromV3 } from '$lib/api/client';
import {
  referenceSystemDetail,
  v3Body,
  v3Ring,
  v3Station,
  v3System,
} from '$lib/api/v3-system-fixtures';

import {
  bodyFamilyFromClass,
  buildSystemScene,
  fitSystemCameraToViewport,
  stellarPresentationSubtype,
  SYSTEM_OVERVIEW_REFERENCE_ASPECT,
  systemBodyLayout,
  systemOrbitGuides,
} from './system-scene';
import { stellarPresentation } from './stellar-presentation';
import type { BodyFamily } from './contracts';

const fixture = referenceSystemDetail();

describe('planet class presentation', () => {
  it.each<[string | null, BodyFamily]>([
    ['Earth-like world', { family: 'earth-like' }],
    ['Water world', { family: 'water-world' }],
    ['Ammonia world', { family: 'ammonia-world' }],
    ['High metal content world', { family: 'high-metal-content' }],
    ['Metal-rich body', { family: 'metal-rich' }],
    ['Rocky body', { family: 'rocky' }],
    ['Rocky Ice world', { family: 'rocky-ice' }],
    ['Rocky ice body', { family: 'rocky-ice' }],
    ['Icy body', { family: 'icy' }],
    ['Class I gas giant', { family: 'gas-giant', variant: 'I' }],
    ['Class II gas giant', { family: 'gas-giant', variant: 'II' }],
    ['Class III gas giant', { family: 'gas-giant', variant: 'III' }],
    ['Class IV gas giant', { family: 'gas-giant', variant: 'IV' }],
    ['Class V gas giant', { family: 'gas-giant', variant: 'V' }],
    [
      'Gas giant with water-based life',
      { family: 'gas-giant-life', variant: 'water' },
    ],
    [
      'Gas giant with ammonia-based life',
      { family: 'gas-giant-life', variant: 'ammonia' },
    ],
    [
      'Gas giant with water based life',
      { family: 'gas-giant-life', variant: 'water' },
    ],
    [
      'Gas giant with ammonia based life',
      { family: 'gas-giant-life', variant: 'ammonia' },
    ],
    ['Helium-rich gas giant', { family: 'helium-gas-giant' }],
    ['Helium gas giant', { family: 'helium-gas-giant' }],
    ['Water giant', { family: 'water-giant' }],
    [null, { family: 'unknown' }],
    ['', { family: 'unknown' }],
    ['Uncatalogued gas giant', { family: 'unknown' }],
    ['Earth', { family: 'unknown' }],
  ])('maps the retained class %s without guessing', (bodyClass, family) => {
    expect(bodyFamilyFromClass(bodyClass)).toEqual(family);
  });
});

describe('system scene', () => {
  it('gives Sol-like planets distinct families while retaining the raw class and provenance', () => {
    const detail = systemDetailFromV3(
      v3System({ name: 'Sol' }),
      [
        v3Body({
          name: 'Sol',
          body_class: 'G (White-Yellow) Star',
          body_class_source: 'ratings-v4-derived',
        }),
        ...[
          ['Earth', 'Earth-like world'],
          ['Jupiter', 'Class I gas giant'],
          ['Europa', 'Icy body'],
          ['Earth-like world', null],
          ['Unfamiliar', '  Unrecognised class  '],
        ].map(([name, bodyClass], index) =>
          v3Body({
            body_id: String(index + 1),
            frontier_body_id: String(index + 1),
            body_type: 'planet',
            name: name!,
            body_class: bodyClass,
            body_class_source: bodyClass ? 'ratings-v4-derived' : null,
            radius_km: 6_371,
            planet_mass_earth: 1,
          }),
        ),
      ],
      [],
      [],
    );
    const scene = buildSystemScene(detail, 1);
    const bodies = [...scene.bodies].sort(
      (a, b) => a.ref.bodyId - b.ref.bodyId,
    );
    expect(bodies.map((body) => body.bodyFamily)).toEqual([
      undefined,
      { family: 'earth-like' },
      { family: 'gas-giant', variant: 'I' },
      { family: 'icy' },
      { family: 'unknown' },
      { family: 'unknown' },
    ]);
    expect(bodies[1]?.bodyClass).toEqual({
      value: 'Earth-like world',
      representation: 'AUTHORITATIVE',
      provenance: [{ source: 'ratings-v4-derived' }],
    });
    expect(bodies[4]?.bodyClass).toBeUndefined();
    expect(bodies[5]?.bodyClass?.value).toBe('  Unrecognised class  ');
    expect(bodies[0]?.subtype?.value).toBe('G (G2 V) Star');
    expect(bodies[0]?.bodyClass).toBeUndefined();
  });

  it('keeps V3 body facts authoritative and layout explicitly schematic', () => {
    const scene = buildSystemScene(fixture, 4);
    expect(scene.kind).toBe('system');
    expect(scene.fidelity).toBe('S1');
    // Only bodies with a unique Frontier BodyID are placed.
    expect(scene.bodies.map((body) => body.ref.bodyId)).toEqual([0, 3]);
    expect(scene.bodies[0]).toMatchObject({
      name: 'Reference System',
      class: { value: 'Star', representation: 'AUTHORITATIVE' },
      subtype: { value: 'G (G2 V) Star', representation: 'DERIVED' },
      physicalRadiusM: { value: 695_700_000 },
      rings: { state: 'UNKNOWN', bands: [] },
    });
    expect(scene.bodies[1]).toMatchObject({
      name: 'Reference System 3',
      class: { value: 'Planet', representation: 'AUTHORITATIVE' },
      bodyFamily: { family: 'unknown' },
      distanceFromArrivalLs: {
        value: 11_450,
        representation: 'AUTHORITATIVE',
      },
      orbital: { placement: 'DETERMINISTIC_SCHEMATIC' },
      rings: {
        state: 'PRESENT',
        bands: [
          {
            id: '900719925632971229',
            ringClass: { value: 'icy' },
            innerRadiusM: { value: 90_000_000 },
            outerRadiusM: { value: 130_000_000 },
          },
        ],
      },
    });
    expect(scene.bodies[1]).not.toHaveProperty('subtype');
  });

  it('replaces the shared flat spiral with a known-parent region', () => {
    const scene = buildSystemScene(fixture, 4);
    const layout = systemBodyLayout(scene.bodies);
    expect(layout[0]?.position.x).toBeCloseTo(0);
    expect(layout[1]?.parentBodyId).toBe(0);
    expect(layout[1]?.group).toBe('attached');
    expect(layout[1]!.orbitRadius).toBeGreaterThan(
      layout[0]!.body.displayRadius + layout[1]!.body.displayRadius,
    );
    expect(
      systemOrbitGuides(layout).map((guide) => guide.parentBodyId),
    ).toEqual([0]);
    expect(layout[1]?.position.y).toBe(0);
    expect(systemBodyLayout(scene.bodies)).toEqual(layout);
  });

  it('retains the previous range and glyph sizes for a two-body system', () => {
    const scene = buildSystemScene(fixture, 4);
    expect(scene.camera.semanticDistance).toBe(38);
    expect(scene.bodies.map((body) => body.displayRadius)).toEqual([2.2, 0.68]);
  });

  it('focuses a known selected body without inventing missing body ids', () => {
    const scene = buildSystemScene(fixture, 8, 3);
    expect(scene.bodies).toHaveLength(2);
    expect(scene.camera.focus).toEqual({
      kind: 'body',
      ref: { systemId64: fixture.id64, bodyId: 3 },
    });
    expect(buildSystemScene(fixture, 9, 99).camera.focus).toEqual({
      kind: 'system',
      systemId64: fixture.id64,
    });
  });

  it('omits bodies, rings and stations the generation no longer holds as current', () => {
    const detail = systemDetailFromV3(
      v3System({ lifecycle_state: 'MISSING_CANDIDATE' }),
      [
        v3Body({ body_id: '1', frontier_body_id: '0', name: 'Current' }),
        v3Body({
          body_id: '2',
          frontier_body_id: '0',
          name: 'Retired',
          lifecycle_state: 'RETIRED',
        }),
        v3Body({
          body_id: '3',
          frontier_body_id: '4',
          name: 'Missing',
          lifecycle_state: 'MISSING_CANDIDATE',
        }),
      ],
      [
        v3Ring({ ring_id: '10', body_id: '1' }),
        v3Ring({ ring_id: '11', body_id: '1', lifecycle_state: 'RETIRED' }),
      ],
      [
        v3Station({ station_id: '20' }),
        v3Station({ station_id: '21', lifecycle_state: 'RETIRED' }),
      ],
    );
    expect(detail.lifecycleState).toBe('MISSING_CANDIDATE');
    expect(detail.bodies.map((body) => body.name)).toEqual(['Current']);
    // A retired body sharing the BodyID no longer makes the current one ambiguous.
    expect(detail.bodies[0]?.id).toBe(0);
    expect(detail.bodies[0]?.rings.map((ring) => ring.ringKey)).toEqual(['10']);
    expect(detail.stations.map((station) => station.stationKey)).toEqual([
      '20',
    ]);
  });

  it('never selects by an ambiguous or unsafe Frontier BodyID', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        v3Body({ body_id: '1', frontier_body_id: '7', name: 'A' }),
        v3Body({ body_id: '2', frontier_body_id: '7', name: 'B' }),
        v3Body({
          body_id: '3',
          frontier_body_id: '9007199254740993',
          name: 'C',
        }),
        v3Body({ body_id: '4', frontier_body_id: '05', name: 'D' }),
      ],
      [],
      [],
    );
    expect(detail.bodies.map((body) => body.id)).toEqual([
      null,
      null,
      null,
      null,
    ]);
    expect(buildSystemScene(detail, 1).fidelity).toBe('S0');
  });

  it('phrases catalogued stellar classes so the shared presentation recognises them', () => {
    const glyph = (spectral: string, luminosity: string | null = null) =>
      stellarPresentation({
        type: 'Star',
        subtype:
          stellarPresentationSubtype({
            bodyType: 'star',
            spectralClass: spectral,
            luminosityClass: luminosity,
          }) ?? undefined,
      }).glyph;
    expect(glyph('G2', 'V')).toBe('main-sequence-g');
    expect(glyph('M5', 'Va')).toBe('main-sequence-m');
    expect(glyph('K3', 'III')).toBe('giant-warm');
    expect(glyph('DA')).toBe('white-dwarf');
    expect(glyph('N')).toBe('neutron');
    expect(glyph('H')).toBe('black-hole');
    // Catalogued classes carry numeric subclasses.
    expect(glyph('N0')).toBe('neutron');
    expect(glyph('H0')).toBe('black-hole');
    expect(glyph('DA6')).toBe('white-dwarf');
    expect(glyph('WC8')).toBe('wolf-rayet');
    expect(glyph('Y3')).toBe('brown-dwarf-y-t');
    expect(glyph('WC')).toBe('wolf-rayet');
    expect(glyph('TTS')).toBe('ttauri-herbig');
    expect(
      stellarPresentationSubtype({
        bodyType: 'planet',
        spectralClass: 'G2',
        luminosityClass: null,
      }),
    ).toBeNull();
  });
});

const hierarchyBody = (
  id: number,
  name: string,
  parentId: number | null,
  axis: number | null,
  overrides: Parameters<typeof v3Body>[0] = {},
) =>
  v3Body({
    body_id: String(1000 + id),
    frontier_body_id: String(id),
    direct_parent_body_id: parentId === null ? null : String(1000 + parentId),
    name,
    body_type: 'planet',
    is_main_star: false,
    spectral_class: null,
    luminosity_class: null,
    radius_km: 6000,
    semi_major_axis_km: axis,
    distance_from_arrival_ls: null,
    ...overrides,
  });

function hierarchyFixture() {
  return systemDetailFromV3(
    v3System(),
    [
      hierarchyBody(0, 'Sol', null, null, {
        body_type: 'star',
        is_main_star: true,
        distance_from_arrival_ls: 0,
      }),
      hierarchyBody(1, 'Earth', 0, 150_000_000),
      hierarchyBody(2, 'Luna', 1, 384_400),
      hierarchyBody(3, 'Nested moon', 2, 5_000),
      hierarchyBody(4, 'Mars', 0, 228_000_000),
      hierarchyBody(5, 'Phobos', 4, 9_000),
      hierarchyBody(6, 'Deimos', 4, 23_000),
      hierarchyBody(7, 'Binary centre', null, null, {
        body_type: 'barycentre',
        distance_from_arrival_ls: 0,
      }),
      hierarchyBody(8, 'Binary A', 7, 12_000_000, { body_type: 'star' }),
      hierarchyBody(9, 'Binary B', 7, 24_000_000, { body_type: 'star' }),
      hierarchyBody(10, 'Lost body', 999, 1_000),
      hierarchyBody(11, 'Moon without distance', 1, null),
    ],
    [],
    [],
  );
}

describe('hierarchy-aware system layout', () => {
  it('builds known star, planet, nested moon and barycentre groups without crossing sibling bands', () => {
    const scene = buildSystemScene(hierarchyFixture(), 1);
    const layout = systemBodyLayout(scene.bodies);
    const byId = new Map(layout.map((item) => [item.body.ref.bodyId, item]));
    for (const [id, parentId] of [
      [1, 0],
      [2, 1],
      [3, 2],
      [4, 0],
      [5, 4],
      [6, 4],
      [8, 7],
      [9, 7],
    ]) {
      expect(byId.get(id!)!).toMatchObject({
        group: 'attached',
        parentBodyId: parentId,
        placement: 'placed',
      });
    }
    expect(byId.get(0)).toMatchObject({ group: 'root', parentBodyId: null });
    expect(byId.get(7)).toMatchObject({ group: 'root', parentBodyId: null });
    const earth = byId.get(1)!;
    const mars = byId.get(4)!;
    expect(earth.orbitRadius + earth.regionRadius).toBeLessThan(
      mars.orbitRadius - mars.regionRadius,
    );
    expect(byId.get(2)!.orbitRadius).toBeLessThan(earth.orbitRadius);
    expect(byId.get(3)!.orbitRadius).toBeLessThan(byId.get(2)!.orbitRadius);
    expect(
      systemOrbitGuides(layout).map((guide) => guide.parentBodyId),
    ).toEqual([0, 0, 1, 2, 4, 4, 7, 7]);
    for (const guide of systemOrbitGuides(layout)) {
      const child = byId.get(guide.bodyId)!;
      expect(guide.radius).toBe(child.orbitRadius);
      expect(guide.position).toEqual(byId.get(guide.parentBodyId)!.position);
      expect(child.placement).toBe('placed');
    }
    expect(layout.every((item) => item.position.y === 0)).toBe(true);
  });

  it('keeps dangling parents unattached and unknown distances marked away from the star', () => {
    const layout = systemBodyLayout(
      buildSystemScene(hierarchyFixture(), 1).bodies,
    );
    const star = layout.find((item) => item.body.ref.bodyId === 0)!;
    const missing = layout.find((item) => item.body.ref.bodyId === 10)!;
    const unknownDistance = layout.find((item) => item.body.ref.bodyId === 11)!;
    expect(missing).toMatchObject({
      group: 'unattached',
      parentBodyId: null,
      placement: 'unplaced',
      orbitRadius: 0,
      positionUnknownReason: 'parent unavailable',
    });
    expect(unknownDistance).toMatchObject({
      group: 'attached',
      parentBodyId: 1,
      placement: 'unplaced',
      orbitRadius: 0,
    });
    expect(missing.position).not.toEqual(star.position);
    expect(unknownDistance.position).not.toEqual(star.position);
    expect(missing.body.directParentBodyKey).toBe('1999');
    const earth = layout.find((item) => item.body.ref.bodyId === 1)!;
    const moons = layout.filter(
      (item) => item.parentBodyId === 1 && item.placement === 'placed',
    );
    expect(
      unknownDistance.position.x + unknownDistance.regionRadius,
    ).toBeLessThan(
      earth.position.x -
        Math.max(...moons.map((item) => item.orbitRadius + item.regionRadius)),
    );
    expect(unknownDistance.position.z).toBeCloseTo(earth.position.z);
    expect(systemOrbitGuides(layout).some((guide) => guide.bodyId === 11)).toBe(
      false,
    );
  });

  it('does not re-phase known siblings when an unknown-distance child is added', () => {
    const scene = buildSystemScene(hierarchyFixture(), 1);
    const withUnknown = systemBodyLayout(scene.bodies);
    const withoutUnknown = systemBodyLayout(
      scene.bodies.filter((body) => body.ref.bodyId !== 11),
    );
    const localAngle = (
      layout: ReturnType<typeof systemBodyLayout>,
      id: number,
    ) => {
      const child = layout.find((item) => item.body.ref.bodyId === id)!;
      const parent = layout.find(
        (item) => item.body.ref.bodyId === child.parentBodyId,
      )!;
      return Math.atan2(
        child.position.z - parent.position.z,
        child.position.x - parent.position.x,
      );
    };
    for (const id of [1, 2, 3, 4, 5, 6, 8, 9]) {
      expect(localAngle(withUnknown, id)).toBeCloseTo(
        localAngle(withoutUnknown, id),
      );
    }
  });

  it('sorts by semi-major axis, falls back to arrival distance in kilometres, then name and canonical key', () => {
    const bodies = [
      hierarchyBody(0, 'Sol', null, null, {
        body_type: 'star',
        distance_from_arrival_ls: 0,
      }),
      hierarchyBody(1, 'Outer axis', 0, 200, { distance_from_arrival_ls: 1 }),
      hierarchyBody(2, 'Inner axis', 0, 100, {
        distance_from_arrival_ls: 9999,
      }),
      hierarchyBody(3, 'Arrival fallback', 0, null, {
        distance_from_arrival_ls: 0.001,
      }),
      hierarchyBody(4, 'Z tie', 0, 400),
      hierarchyBody(5, 'A tie', 0, 400),
      hierarchyBody(6, 'Zulu unknown', 0, null),
      hierarchyBody(7, 'Alpha unknown', 0, null),
      hierarchyBody(8, 'Alpha unknown', 0, null),
    ];
    const layout = systemBodyLayout(
      buildSystemScene(systemDetailFromV3(v3System(), bodies, [], []), 1)
        .bodies,
    );
    const parent = layout.find((item) => item.body.ref.bodyId === 0)!;
    const byRadius = layout
      .filter((item) => item.parentBodyId === 0)
      .sort(
        (a, b) =>
          Math.hypot(
            a.position.x - parent.position.x,
            a.position.z - parent.position.z,
          ) -
          Math.hypot(
            b.position.x - parent.position.x,
            b.position.z - parent.position.z,
          ),
      );
    expect(byRadius.map((item) => item.body.ref.bodyId)).toEqual([
      2, 1, 3, 4, 5, 7, 8, 6,
    ]);
  });

  it('does not round close axes to the same sort key when logarithmic distances coincide', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        hierarchyBody(0, 'Star', null, null, {
          body_type: 'star',
          distance_from_arrival_ls: 0,
        }),
        hierarchyBody(1, 'Outer', 0, 1e12 + 0.001),
        hierarchyBody(2, 'Inner', 0, 1e12),
      ],
      [],
      [],
    );
    const layout = systemBodyLayout(buildSystemScene(detail, 1).bodies);
    expect(
      layout.find((item) => item.body.ref.bodyId === 2)!.orbitRadius,
    ).toBeLessThan(
      layout.find((item) => item.body.ref.bodyId === 1)!.orbitRadius,
    );
  });

  it('keeps positions and membership deterministic across revisions, selection and input permutations', () => {
    const detail = hierarchyFixture();
    const layout = systemBodyLayout(buildSystemScene(detail, 1).bodies);
    const reordered = systemBodyLayout(
      buildSystemScene(
        { ...detail, bodies: [...detail.bodies].reverse() },
        99,
        6,
      ).bodies,
    );
    expect(
      new Map(reordered.map((item) => [item.body.ref.bodyId, item])),
    ).toEqual(new Map(layout.map((item) => [item.body.ref.bodyId, item])));
  });

  it('does not turn omitted or ambiguous Frontier identities into roots or guess a parent', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        hierarchyBody(0, 'Unselectable barycentre', null, null, {
          body_type: 'barycentre',
          frontier_body_id: null,
        }),
        hierarchyBody(1, 'Member star', 0, 100, { body_type: 'star' }),
      ],
      [],
      [],
    );
    const scene = buildSystemScene(detail, 1);
    expect(scene.bodies).toHaveLength(1);
    expect(systemBodyLayout(scene.bodies)[0]).toMatchObject({
      group: 'unattached',
      placement: 'unplaced',
      parentBodyId: null,
    });
    expect(scene.bodies[0]!.ref.bodyId).toBe(1);
  });

  it('handles cyclic parent links without recursion or silently choosing a different parent', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        hierarchyBody(1, 'A', 2, 100),
        hierarchyBody(2, 'B', 1, 200),
        hierarchyBody(3, 'Self', 3, 300),
        hierarchyBody(4, 'Known child of A', 1, 10),
      ],
      [],
      [],
    );
    const layout = systemBodyLayout(buildSystemScene(detail, 1).bodies);
    for (const item of layout.filter((item) => item.body.ref.bodyId !== 4)) {
      expect(item).toMatchObject({
        group: 'unattached',
        placement: 'unplaced',
        parentBodyId: null,
        positionUnknownReason: 'cyclic parent links',
      });
      expect(Number.isFinite(item.position.x)).toBe(true);
    }
    expect(
      layout.find((item) => item.body.ref.bodyId === 4)!.parentBodyId,
    ).toBe(1);
  });

  it('marks missing, non-positive and non-finite distances as unknown except a known root origin', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        hierarchyBody(0, 'Star', null, null, {
          body_type: 'star',
          distance_from_arrival_ls: 0,
        }),
        ...[null, 0, -1, Infinity, NaN].map((distance, index) =>
          hierarchyBody(index + 1, `Unknown ${index}`, 0, distance, {
            distance_from_arrival_ls: distance,
          }),
        ),
      ],
      [],
      [],
    );
    const layout = systemBodyLayout(buildSystemScene(detail, 1).bodies);
    expect(layout.find((item) => item.body.ref.bodyId === 0)!.placement).toBe(
      'placed',
    );
    expect(
      layout
        .filter((item) => item.body.ref.bodyId !== 0)
        .every((item) => item.placement === 'unplaced'),
    ).toBe(true);
  });

  it('keeps pathological deep hierarchy bounded and explicitly unplaced', () => {
    const detail = systemDetailFromV3(
      v3System(),
      Array.from({ length: 2000 }, (_, id) =>
        hierarchyBody(id, `Body ${id}`, id === 0 ? null : id - 1, 1000),
      ),
      [],
      [],
    );
    const layout = systemBodyLayout(buildSystemScene(detail, 1).bodies);
    expect(layout).toHaveLength(2000);
    expect(
      layout.every(
        (item) =>
          item.placement === 'unplaced' &&
          item.orbitRadius === 0 &&
          item.regionRadius >= Math.max(0.55, item.body.displayRadius) &&
          Number.isFinite(item.position.x) &&
          Number.isFinite(item.position.z),
      ),
    ).toBe(true);
    expect(layout[1]!.parentBodyId).toBe(0);
    expect(systemOrbitGuides(layout)).toEqual([]);
    expect(layout[0]!.positionUnknownReason).toBe(
      'hierarchy exceeds schematic range',
    );
  });
});

describe('system overview viewport fit', () => {
  const camera = {
    systemId64: '42',
    focus: { kind: 'system', systemId64: '42' },
    semanticDistance: 153,
    bearingRad: -0.85,
    pitchRad: 0.58,
    revision: 1,
  } as const;

  it('keeps the contract distance at the reference aspect or wider', () => {
    expect(
      fitSystemCameraToViewport(camera, { width: 1200, height: 684 }),
    ).toBe(camera);
    expect(
      fitSystemCameraToViewport(camera, { width: 2400, height: 684 }),
    ).toBe(camera);
  });

  it('widens the overview for narrower canvases by the aspect ratio', () => {
    const square = fitSystemCameraToViewport(camera, {
      width: 600,
      height: 600,
    });
    expect(square.semanticDistance).toBe(
      Math.ceil(153 * SYSTEM_OVERVIEW_REFERENCE_ASPECT),
    );
    const phone = fitSystemCameraToViewport(camera, {
      width: 375,
      height: 470,
    });
    expect(phone.semanticDistance).toBeGreaterThan(square.semanticDistance);
    expect(phone).toMatchObject({ bearingRad: -0.85, pitchRad: 0.58 });
  });

  it('ignores a degenerate viewport', () => {
    expect(fitSystemCameraToViewport(camera, { width: 0, height: 0 })).toBe(
      camera,
    );
  });
});
