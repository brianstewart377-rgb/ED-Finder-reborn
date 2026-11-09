import { describe, expect, it } from 'vitest';
import type { PlanBodiesV1, PlanFacilityV1, PlanSystemBodyV1 } from '$lib/api';
import { colonyProjectsCodec } from '$lib/persistence/storage';
import { resolvePlanLocations } from './locations';
import preP5bExport from './__fixtures__/pre-p5b-plan-export.json';
import {
  MAX_IMPORT_BYTES,
  PRIMARY_PORT_PROTECTION,
  abandonPlan,
  addSite,
  canMoveSite,
  countLocationSlots,
  countUsedSlots,
  locationAliases,
  locationSlots,
  emptyPlan,
  exportPlan,
  facilitySlotKind,
  findFreeLocation,
  findPlannerProject,
  importPlan,
  insertSiteBefore,
  isProtectedPrimary,
  moveSite,
  plannerToRecord,
  primaryPortLocks,
  recordToPlanner,
  rejectInvalidLocations,
  removeSite,
  replacePlan,
  setSlotCount,
  toPlanRequest,
  updateSite,
  unplaceMissingLocations,
  type PlannerModel,
  type PlannerSite,
} from './model';

function site(id: string, overrides: Partial<PlannerSite> = {}): PlannerSite {
  return {
    schema: 'cpe_site_v1',
    site_id: id,
    facility_id: 'outpost',
    body_ref: '1',
    slot_kind: 'orbital',
    status: 'planned',
    name: '',
    ...overrides,
  };
}

function plan(...sites: PlannerSite[]): PlannerModel {
  return { ...emptyPlan('42'), sites };
}

function body(id: string, type = 'planet'): PlanSystemBodyV1 {
  return {
    body_id: id,
    name: `Test ${id}`,
    direct_parent_body_id: null,
    body_type: type,
    is_main_star: false,
    is_landable: true,
    radius_km: null,
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

function bodies(): PlanBodiesV1 {
  return {
    system_id64: '42',
    generation_id: 'fixture',
    publication_sequence: '1',
    bodies: [
      body('1'),
      body('2', 'barycentre'),
      { ...body('3', 'belt cluster'), name: 'Test A Belt Cluster 8' },
      { ...body('4', 'belt cluster'), name: 'Test A Belt Cluster 1' },
    ],
    belts: [
      {
        belt_id: '10',
        parent_body_id: '1',
        name: 'Test A Belt',
        ring_type: null,
        reserve: null,
        mass_mt: null,
        inner_radius_km: null,
        outer_radius_km: null,
        lifecycle_state: 'ACTIVE',
      },
    ],
  };
}

describe('planner persistence codec', () => {
  it('round trips through the existing colonyProjects envelope and keeps V2 items and other assignments exactly', () => {
    const model = setSlotCount(
      plan(
        site('one'),
        site('two', { body_ref: null, name: 'Named installation' }),
      ),
      '1',
      'orbital',
      4,
    );
    const legacy = {
      facility_template_id: 'old-v2',
      local_body_id: 9,
      arbitrary: { source_id64: 123, unknown: [false, null] },
      is_primary_port: true,
    };
    const existing = plannerToRecord(emptyPlan('42'), undefined, {
      id: 'legacy-project',
      now: '2026-01-01',
      systemName: 'Test',
    });
    existing.build_plan_placements = [legacy];
    existing.selected_body_assignments = {
      old_assignment: { system_id64: 123, unknown: 'preserved' },
    };
    const record = plannerToRecord(model, existing, { now: '2026-02-01' });
    const decoded = colonyProjectsCodec.decode(
      colonyProjectsCodec.encode({
        version: 3,
        state: { projects: { [record.id]: record } },
      }),
    );
    expect(decoded.ok).toBe(true);
    if (!decoded.ok) throw new Error('Expected stored plan to hydrate');
    const restored = decoded.value.state.projects[record.id];
    expect(recordToPlanner(restored)).toEqual(model);
    expect(restored.build_plan_placements[0]).toEqual(legacy);
    expect(restored.selected_body_assignments.old_assignment).toEqual(
      existing.selected_body_assignments.old_assignment,
    );
    expect(restored.id).toBe('legacy-project');
    expect(restored.created_at).toBe('2026-01-01');
    expect(restored.updated_at).toBe('2026-02-01');
  });

  it('follows the colony-system-UUID ID convention and reuses the planner project', () => {
    const old = plannerToRecord(emptyPlan('42'));
    const current = plannerToRecord(plan(site('one')));
    expect(current.id).toMatch(/^colony-42-[a-f\d-]+$/);
    expect(findPlannerProject({ old, current }, '42')?.id).toBe(current.id);
    expect(findPlannerProject({ current }, '43')).toBeUndefined();
    expect(() => plannerToRecord(emptyPlan('43'), old)).toThrow(
      'another system',
    );
  });

  it('writes just planner-owned slots and preserves unknown counts as null', () => {
    const model = setSlotCount(emptyPlan('42'), 'belt:10', 'orbital', 0);
    expect(model.slots).toEqual({
      'belt:10': { orbital_slots: 0, surface_slots: null },
    });
    expect(recordToPlanner(plannerToRecord(model))).toEqual(model);
    expect(() => setSlotCount(model, '1', 'surface', 65)).toThrow('0 to 64');
    expect(() => setSlotCount(model, '1', 'surface', 1.5)).toThrow('0 to 64');
  });
});

describe('planner request and slots', () => {
  it('uses the API surface prediction in display, evaluation and free-slot selection without saving it', () => {
    const data = bodies();
    Object.assign(data.bodies[0], {
      predicted_surface_slots: 5,
      prediction_status: 'predicted',
    });
    const model = emptyPlan('42');
    expect(locationSlots(model, data, '1')).toEqual({
      orbital_slots: null,
      surface_slots: 5,
    });
    expect(toPlanRequest(model, data).bodies?.[0]).toMatchObject({
      orbital_slots: null,
      surface_slots: 5,
    });
    expect(
      findFreeLocation(
        model,
        resolvePlanLocations(data),
        { allowed_location: 'surface' } as PlanFacilityV1,
        data,
      ),
    ).toBe('1');
    expect(model.slots).toEqual({});
    expect(
      plannerToRecord(model).selected_body_assignments.cpe_slots_v1,
    ).toEqual({});
  });

  it.each([0, 7])(
    'uses entered surface count %s ahead of a prediction and restores the prediction when cleared',
    (count) => {
      const data = bodies();
      Object.assign(data.bodies[0], {
        predicted_surface_slots: 5,
        prediction_status: 'predicted',
      });
      const model = setSlotCount(emptyPlan('42'), '1', 'surface', count);
      expect(locationSlots(model, data, '1')?.surface_slots).toBe(count);
      expect(toPlanRequest(model, data).bodies?.[0].surface_slots).toBe(count);
      const cleared = setSlotCount(model, '1', 'surface', null);
      expect(locationSlots(cleared, data, '1')?.surface_slots).toBe(5);
      expect(toPlanRequest(cleared, data).bodies?.[0].surface_slots).toBe(5);
      expect(cleared.slots['1'].surface_slots).toBeNull();
    },
  );

  it.each([
    { prediction_status: 'unknown', predicted_surface_slots: null },
    { prediction_status: null, predicted_surface_slots: null },
    { prediction_status: 'unknown', predicted_surface_slots: 5 },
    {
      prediction_status: 'predicted',
      predicted_surface_slots: 5,
      is_landable: false,
    },
  ])('keeps unknown or unavailable predictions unknown: %j', (prediction) => {
    const data = bodies();
    Object.assign(data.bodies[0], prediction);
    const model = emptyPlan('42');
    expect(locationSlots(model, data, '1')).toBeUndefined();
    expect(toPlanRequest(model, data).bodies?.[0].surface_slots).toBeNull();
  });

  it('passes published body and belt economy facts through to evaluation', () => {
    const data = bodies();
    data.economy_facts_generation_id = 'derived-fixture';
    Object.assign(data.bodies[0], {
      economy_kind: 'high_metal_content_world',
      economy_parent_id: '2',
      in_binary_pair: true,
      tidally_locked: false,
      volcanism_present: true,
      terraformable: false,
      has_rings: true,
      geological_signals: 3,
      biological_signals: 2,
      signals_complete: true,
    });
    Object.assign(data.bodies[1], {
      economy_kind: null,
      economy_parent_id: null,
      in_binary_pair: null,
      tidally_locked: null,
      volcanism_present: null,
      terraformable: null,
    });
    const request = toPlanRequest(plan(site('port')), data);
    expect(request.bodies?.[0].economy).toEqual({
      kind: 'high_metal_content_world',
      parent_ref: '2',
      in_binary_pair: true,
      tidally_locked: false,
      volcanism: true,
      terraformable: false,
      has_rings: true,
      geological_signals: 3,
      biological_signals: 2,
    });
    expect(request.bodies?.[1].economy).toEqual({
      kind: null,
      parent_ref: null,
      in_binary_pair: null,
      tidally_locked: null,
      volcanism: null,
      terraformable: null,
      has_rings: false,
      geological_signals: null,
      biological_signals: null,
    });
    expect(request.bodies?.[2]).toMatchObject({
      body_ref: '4',
      economy: { kind: null, parent_ref: '1', has_rings: true },
    });
    // A belt with no surviving cluster keeps the same facts on its fallback.
    data.belts[0].name = 'Missing cluster';
    expect(toPlanRequest(emptyPlan('42'), data).bodies?.[2]).toMatchObject({
      body_ref: 'belt:10',
      economy: { kind: null, parent_ref: '1', has_rings: true },
    });
  });

  it.each([
    [false, 0, 0, null, null],
    [null, 0, 0, null, null],
    [true, 0, 0, 0, 0],
    [false, 2, 0, 2, null],
    [null, 0, 3, null, 3],
    [true, 2, 3, 2, 3],
  ] as const)(
    'preserves signal knowledge with survey %s and counts %s / %s',
    (signals_complete, geological_signals, biological_signals, geo, bio) => {
      const data = bodies();
      data.economy_facts_generation_id = 'derived-fixture';
      Object.assign(data.bodies[0], {
        signals_complete,
        geological_signals,
        biological_signals,
      });
      expect(
        toPlanRequest(emptyPlan('42'), data).bodies?.[0].economy,
      ).toMatchObject({ geological_signals: geo, biological_signals: bio });
    },
  );

  it.each([
    ['pristine', true, false, true],
    ['common', false, true, false],
    [null, null, null, null],
  ] as const)(
    'passes system economy facts with reserve %s and null flags as false',
    (reserve_level, has_black_hole, has_white_dwarf, has_neutron_star) => {
      const data = bodies();
      Object.assign(data, {
        economy_facts_generation_id: 'derived-fixture',
        reserve_level,
        has_black_hole,
        has_white_dwarf,
        has_neutron_star,
      });
      expect(toPlanRequest(emptyPlan('42'), data).system_economy).toEqual({
        reserve: reserve_level,
        has_black_hole: has_black_hole ?? false,
        has_white_dwarf: has_white_dwarf ?? false,
        has_neutron_star: has_neutron_star ?? false,
      });
    },
  );

  it('sends no economy objects when the derived generation is unavailable', () => {
    const data = bodies();
    Object.assign(data, {
      economy_facts_generation_id: null,
      reserve_level: 'pristine',
      has_black_hole: true,
      has_white_dwarf: true,
      has_neutron_star: true,
    });
    data.bodies[0].economy_kind = 'rocky_body';
    const request = toPlanRequest(emptyPlan('42'), data);
    expect(request).not.toHaveProperty('system_economy');
    for (const body of request.bodies ?? [])
      expect(body).not.toHaveProperty('economy');
  });

  it('maps every non-cluster body and one resolved belt to the API with no computed CP', () => {
    const model = setSlotCount(
      setSlotCount(plan(site('one', { body_ref: '4' })), '1', 'surface', 7),
      '4',
      'orbital',
      5,
    );
    const request = toPlanRequest(model, bodies());
    expect(request.bodies).toEqual([
      {
        body_ref: '1',
        orbital_slots: null,
        surface_slots: 7,
        is_landable: true,
        is_ringed: false,
      },
      {
        body_ref: '2',
        orbital_slots: null,
        surface_slots: null,
        is_landable: true,
        is_ringed: false,
      },
      {
        body_ref: '4',
        orbital_slots: 5,
        surface_slots: 0,
        is_landable: false,
        is_ringed: true,
      },
    ]);
    expect(request.sites).toEqual([
      {
        site_id: 'one',
        facility_id: 'outpost',
        body_ref: '4',
        slot_kind: 'orbital',
        status: 'planned',
        name: '',
      },
    ]);
    expect(() => toPlanRequest(emptyPlan('43'), bodies())).toThrow(
      'different system',
    );
  });

  it('maps fallback belts exactly once and never maps unmatched clusters', () => {
    const data = bodies();
    data.belts[0].name = 'Missing cluster';
    expect(
      toPlanRequest(emptyPlan('42'), data).bodies?.map(
        ({ body_ref }) => body_ref,
      ),
    ).toEqual(['1', '2', 'belt:10']);
  });

  it('evaluates a saved site on a belt cluster the belt no longer selects as that same belt', () => {
    // Cluster 1 ('4') was the belt's location when the site was saved; it is
    // now retired, so the active belt selects Cluster 8 ('3') instead. The
    // saved asteroid base is still on the belt: it must stay ringed, and it
    // must share the belt's one slot total with new builds there.
    const data = bodies();
    data.bodies[3].lifecycle_state = 'RETIRED';
    const model = setSlotCount(
      plan(
        site('port'),
        site('base', {
          body_ref: '4',
          facility_id: 'asteroid_base',
          status: 'complete',
        }),
        site('new-base', { body_ref: '3', facility_id: 'asteroid_base' }),
      ),
      '3',
      'orbital',
      1,
    );
    const request = toPlanRequest(model, data);
    const refs = request.bodies?.map(({ body_ref }) => body_ref) ?? [];
    expect(refs).toEqual(['1', '2', '3']);
    expect(request.bodies?.find(({ body_ref }) => body_ref === '3')).toEqual({
      body_ref: '3',
      orbital_slots: 1,
      surface_slots: 0,
      is_landable: false,
      is_ringed: true,
    });
    expect(
      request.sites.map(({ site_id, body_ref }) => [site_id, body_ref]),
    ).toEqual([
      ['port', '1'],
      ['base', '3'],
      ['new-base', '3'],
    ]);
    // The saved plan itself keeps its original reference.
    expect(model.sites[1].body_ref).toBe('4');
  });

  it('looks up belt capacity and occupancy through every alias, as the plan check does', () => {
    const data = bodies();
    data.bodies[3].lifecycle_state = 'RETIRED';
    const model = setSlotCount(
      plan(
        site('port'),
        site('base', { body_ref: '4', status: 'complete' }),
        site('gone', { body_ref: '4', status: 'demolished' }),
      ),
      '4',
      'orbital',
      2,
    );
    expect(locationAliases(data, '3')).toEqual(['3', '4', 'belt:10']);
    expect(locationAliases(data, '1')).toEqual(['1']);
    expect(locationSlots(model, data, '3')?.orbital_slots).toBe(2);
    expect(countLocationSlots(model, data, '3')).toEqual({
      orbital_slots: 1,
      surface_slots: 0,
    });
    // A total entered on the current reference wins over the old alias's.
    const edited = setSlotCount(model, '3', 'orbital', 3);
    expect(locationSlots(edited, data, '3')?.orbital_slots).toBe(3);
    expect(
      toPlanRequest(edited, data).bodies?.find(
        ({ body_ref }) => body_ref === '3',
      )?.orbital_slots,
    ).toBe(3);
  });

  it('keeps slot counts entered on a belt alias the belt no longer selects', () => {
    const data = bodies();
    data.bodies[3].lifecycle_state = 'RETIRED';
    const model = setSlotCount(
      plan(site('port'), site('base', { body_ref: '4' })),
      '4',
      'orbital',
      2,
    );
    expect(
      toPlanRequest(model, data).bodies?.find(
        ({ body_ref }) => body_ref === '3',
      )?.orbital_slots,
    ).toBe(2);
  });

  it('sends all current locations and only site-referenced non-current bodies and belts, once per reference', () => {
    const data = bodies();
    data.bodies.push(
      body('5'),
      { ...body('6'), lifecycle_state: 'RETIRED', has_rings: true },
      { ...body('7'), lifecycle_state: 'MISSING_CANDIDATE' },
      { ...body('8'), lifecycle_state: 'RETIRED' },
      { ...body('9'), lifecycle_state: 'MISSING_CANDIDATE' },
    );
    data.belts[0].lifecycle_state = 'RETIRED';
    data.belts.push(
      {
        ...data.belts[0],
        belt_id: '20',
        name: 'Current belt',
        lifecycle_state: 'ACTIVE',
      },
      {
        ...data.belts[0],
        belt_id: '30',
        name: 'Missing belt',
        lifecycle_state: 'MISSING_CANDIDATE',
      },
      { ...data.belts[0], belt_id: '40', name: 'Unreferenced retired belt' },
      {
        ...data.belts[0],
        belt_id: '50',
        name: 'Unreferenced missing belt',
        lifecycle_state: 'MISSING_CANDIDATE',
      },
    );
    const model = setSlotCount(
      setSlotCount(
        plan(
          site('current'),
          site('retired-planet', { body_ref: '6' }),
          site('second-retired-planet', { body_ref: '6' }),
          site('missing-planet', { body_ref: '7', status: 'complete' }),
          site('retired-belt', { body_ref: '4' }),
          site('missing-belt', { body_ref: 'belt:30' }),
          site('unplaced', { body_ref: null }),
        ),
        '6',
        'surface',
        3,
      ),
      'belt:30',
      'orbital',
      2,
    );
    const request = toPlanRequest(model, data);
    expect(request.bodies?.map(({ body_ref }) => body_ref)).toEqual([
      '1',
      '2',
      '5',
      '6',
      '7',
      '4',
      'belt:20',
      'belt:30',
    ]);
    expect(request.bodies?.find(({ body_ref }) => body_ref === '6')).toEqual({
      body_ref: '6',
      orbital_slots: null,
      surface_slots: 3,
      is_landable: true,
      is_ringed: true,
    });
    expect(
      request.bodies?.find(({ body_ref }) => body_ref === 'belt:30'),
    ).toEqual({
      body_ref: 'belt:30',
      orbital_slots: 2,
      surface_slots: 0,
      is_landable: false,
      is_ringed: true,
    });
    expect(request.sites?.map(({ body_ref }) => body_ref)).toEqual(
      model.sites.map(({ body_ref }) => body_ref),
    );
  });

  it('preserves an explicit non-current belt reference even when the belt has a cluster alias', () => {
    const data = bodies();
    data.belts[0].lifecycle_state = 'RETIRED';
    const request = toPlanRequest(
      plan(site('belt', { body_ref: 'belt:10' })),
      data,
    );
    expect(request.bodies?.map(({ body_ref }) => body_ref)).toEqual([
      '1',
      '2',
      'belt:10',
    ]);
    expect(request.sites?.[0].body_ref).toBe('belt:10');
  });

  it('keeps a retired belt site on its previously current cluster alias', () => {
    const data = bodies();
    data.bodies[3].lifecycle_state = 'RETIRED';
    data.belts[0].lifecycle_state = 'RETIRED';
    const model = plan(site('old-belt', { body_ref: '3' }));
    const request = toPlanRequest(model, data);
    expect(request.bodies?.map(({ body_ref }) => body_ref)).toEqual([
      '1',
      '2',
      '3',
    ]);
    expect(request.bodies?.[2]).toMatchObject({
      body_ref: '3',
      surface_slots: 0,
      is_landable: false,
      is_ringed: true,
    });
    expect(request.sites?.[0].body_ref).toBe('3');
    expect(() => rejectInvalidLocations(model, data)).toThrow(
      'no longer in the current galaxy data (retired)',
    );
  });

  it('retains a referenced non-current cluster when its active belt moves to a current representative', () => {
    const data = bodies();
    data.bodies[3].lifecycle_state = 'RETIRED';
    expect(
      toPlanRequest(
        plan(site('old-belt', { body_ref: '4' })),
        data,
      ).bodies?.map(({ body_ref }) => body_ref),
      // '4' is still the belt, evaluated under its current reference '3'.
    ).toEqual(['1', '2', '3']);
    expect(
      toPlanRequest(emptyPlan('42'), data).bodies?.map(
        ({ body_ref }) => body_ref,
      ),
    ).toEqual(['1', '2', '3']);
  });

  it.each([
    ['RETIRED', 'retired'],
    ['MISSING_CANDIDATE', 'missing from the latest data'],
  ])(
    'gives an imported %s body or belt its own current-data message',
    (lifecycle_state, reason) => {
      const data = bodies();
      data.bodies[0].lifecycle_state = lifecycle_state;
      data.belts[0].lifecycle_state = lifecycle_state;
      for (const [body_ref, name] of [
        ['1', 'Test 1'],
        ['4', 'Test A Belt'],
        ['belt:10', 'Test A Belt'],
      ])
        expect(() =>
          rejectInvalidLocations(plan(site('old', { body_ref })), data),
        ).toThrow(
          `“${name}” is no longer in the current galaxy data (${reason}). Move this site to a current body, or to Unplaced, before importing.`,
        );
    },
  );

  it('unplaces non-current imported bodies and belts only after confirmation, preserving the source plan', () => {
    const data = bodies();
    data.bodies[0].lifecycle_state = 'RETIRED';
    data.bodies.push(
      { ...body('5'), lifecycle_state: 'MISSING_CANDIDATE' },
      body('6'),
    );
    data.belts[0].lifecycle_state = 'MISSING_CANDIDATE';
    const imported = plan(
      site('retired-body'),
      site('missing-body', { body_ref: '5' }),
      site('missing-belt-alias', { body_ref: '4' }),
      site('missing-belt', { body_ref: 'belt:10' }),
      site('current', { body_ref: '6' }),
    );
    expect(
      unplaceMissingLocations(imported, data).sites.map(
        ({ body_ref }) => body_ref,
      ),
    ).toEqual([null, null, null, null, '6']);
    expect(imported.sites.map(({ body_ref }) => body_ref)).toEqual([
      '1',
      '5',
      '4',
      'belt:10',
      '6',
    ]);
    expect(() =>
      rejectInvalidLocations(unplaceMissingLocations(imported, data), data),
    ).not.toThrow();
  });

  it('rejects imported sites on hidden clusters and barycentres, but accepts the resolved belt', () => {
    expect(() =>
      rejectInvalidLocations(plan(site('hidden', { body_ref: '3' })), bodies()),
    ).toThrow('not a buildable location');
    expect(() =>
      rejectInvalidLocations(
        plan(site('barycentre', { body_ref: '2' })),
        bodies(),
      ),
    ).toThrow('not a buildable location');
    expect(() =>
      rejectInvalidLocations(plan(site('belt', { body_ref: '4' })), bodies()),
    ).not.toThrow();
    expect(() =>
      rejectInvalidLocations(
        plan(site('unknown', { body_ref: 'missing' })),
        bodies(),
      ),
    ).not.toThrow();
  });

  it('explicitly unplaces missing foreign-import locations without rewriting the original model', () => {
    const imported = plan(
      site('missing', { body_ref: 'missing' }),
      site('hidden', { body_ref: '3' }),
      site('belt', { body_ref: '4' }),
    );
    expect(
      unplaceMissingLocations(imported, bodies()).sites.map(
        ({ body_ref }) => body_ref,
      ),
    ).toEqual([null, null, '4']);
    expect(imported.sites[0].body_ref).toBe('missing');
    expect(() =>
      rejectInvalidLocations(
        unplaceMissingLocations(imported, bodies()),
        bodies(),
      ),
    ).not.toThrow();
  });

  it('counts all occupied statuses except demolished, separately per location and slot kind', () => {
    expect(
      countUsedSlots(
        [
          site('1'),
          site('2', { status: 'building' }),
          site('3', { status: 'complete', slot_kind: 'surface' }),
          site('4', { status: 'demolished' }),
          site('5', { body_ref: null }),
          site('6', { body_ref: 'other' }),
        ],
        '1',
      ),
    ).toEqual({ orbital_slots: 2, surface_slots: 1 });
  });

  it.each([
    ['orbital', 'orbital'],
    ['orbital_ringed', 'orbital'],
    ['surface', 'surface'],
  ] as const)(
    'uses %s facilities in %s slots',
    (allowed_location, slotKind) => {
      expect(facilitySlotKind({ allowed_location } as PlanFacilityV1)).toBe(
        slotKind,
      );
    },
  );

  it('places payer suggestions only in known compatible free slots, otherwise unplaced', () => {
    const locations = resolvePlanLocations(bodies());
    const orbital = { allowed_location: 'orbital' } as PlanFacilityV1;
    const ringed = { allowed_location: 'orbital_ringed' } as PlanFacilityV1;
    const model = setSlotCount(
      setSlotCount(plan(site('used')), '1', 'orbital', 1),
      '4',
      'orbital',
      1,
    );
    expect(findFreeLocation(model, locations, orbital)).toBe('4');
    expect(findFreeLocation(model, locations, ringed)).toBe('4');
    expect(
      findFreeLocation(
        addSite(model, site('belt', { body_ref: '4' })),
        locations,
        orbital,
      ),
    ).toBeNull();
    expect(findFreeLocation(emptyPlan('42'), locations, orbital)).toBeNull();
  });
});

describe('planner actions', () => {
  it('appends and inserts immutable planned sites in order', () => {
    const initial = plan(site('primary'), site('last'));
    const appended = addSite(initial, site('new'));
    expect(initial.sites).toHaveLength(2);
    expect(appended.sites.map(({ site_id }) => site_id)).toEqual([
      'primary',
      'last',
      'new',
    ]);
    expect(
      insertSiteBefore(
        initial,
        site('payer', { status: 'complete' }),
        'last',
      ).sites.map(({ site_id, status }) => [site_id, status]),
    ).toEqual([
      ['primary', 'planned'],
      ['payer', 'planned'],
      ['last', 'planned'],
    ]);
    expect(() => insertSiteBefore(initial, site('payer'), 'primary')).toThrow(
      'before the Primary Port',
    );
    expect(() => addSite(initial, site('last'))).toThrow('unique');
  });

  it('allows adjacent planned steps to move without crossing the Primary Port or fixed statuses', () => {
    const initial = plan(
      site('primary'),
      site('first'),
      site('last'),
      site('building', { status: 'building' }),
    );
    expect(
      moveSite(initial, 'last', 'up').sites.map(({ site_id }) => site_id),
    ).toEqual(['primary', 'last', 'first', 'building']);
    expect(
      moveSite(initial, 'first', 'down').sites.map(({ site_id }) => site_id),
    ).toEqual(['primary', 'last', 'first', 'building']);
    for (const [id, direction] of [
      ['primary', 'down'],
      ['first', 'up'],
      ['last', 'down'],
      ['building', 'up'],
    ] as const) {
      expect(canMoveSite(initial, id, direction)).toBe(false);
      expect(() => moveSite(initial, id, direction)).toThrow('planned builds');
    }
  });

  it('renames, moves, changes status and removes non-protected sites', () => {
    const initial = plan(site('primary'), site('second'));
    const updated = updateSite(initial, 'second', {
      name: 'Mine',
      status: 'complete',
      body_ref: '2',
    });
    expect(updated.sites[1]).toMatchObject({
      name: 'Mine',
      status: 'complete',
      body_ref: '2',
    });
    expect(initial.sites[1].name).toBe('');
    expect(removeSite(updated, 'second').sites).toHaveLength(1);
    expect(() =>
      updateSite(initial, 'second', { name: 'x'.repeat(65) }),
    ).toThrow('64');
  });

  it('protects a completed Primary Port from move, facility or status changes and import replacement', () => {
    const initial = plan(site('primary', { status: 'complete' }), site('next'));
    expect(() => moveSite(initial, 'primary', 'down')).toThrow();
    for (const patch of [
      { body_ref: 'other' },
      { facility_id: 'other' },
      { status: 'planned' as const },
      { slot_kind: 'surface' as const },
    ])
      expect(() => updateSite(initial, 'primary', patch)).toThrow('RB-0008');
    expect(updateSite(initial, 'primary', { name: 'Home' }).sites[0].name).toBe(
      'Home',
    );
    expect(() => replacePlan(initial, plan())).toThrow('RB-0008');
    expect(() =>
      replacePlan(initial, plan(site('new', { status: 'complete' }))),
    ).toThrow('RB-0008');
    expect(
      replacePlan(initial, updateSite(initial, 'primary', { name: 'Home' }))
        .sites[0].name,
    ).toBe('Home');
  });

  it.each(['planned', 'building', 'complete', 'demolished'] as const)(
    'never removes a %s Primary Port, regardless of its location or the next site',
    (primaryStatus) => {
      for (const body_ref of [null, '1'])
        for (const following of [
          [],
          [site('next-port')],
          [site('installation', { facility_id: 'mining-installation' })],
        ]) {
          const initial = plan(
            site('primary', { status: primaryStatus, body_ref }),
            ...following,
          );
          const original = structuredClone(initial);
          expect(() => removeSite(initial, 'primary')).toThrow(
            "The Primary Port can't be removed. In the game it can only be cancelled together with the whole claim; use Abandon plan to start this system again.",
          );
          expect(initial).toEqual(original);
        }
    },
  );

  it.each(['planned', 'building', 'complete', 'demolished'] as const)(
    'removes a non-first %s site without changing the Primary Port',
    (status) => {
      const initial = plan(
        site('primary', { status: 'complete' }),
        site('installation', { facility_id: 'mining-installation', status }),
      );
      expect(removeSite(initial, 'installation').sites).toEqual([
        initial.sites[0],
      ]);
      expect(initial.sites).toHaveLength(2);
    },
  );

  it('protects a completed primary only once it is placed', () => {
    const initial = plan(
      site('primary', { status: 'complete', body_ref: null }),
    );
    expect(isProtectedPrimary(initial, 'primary')).toBe(false);
    expect(isProtectedPrimary(initial, 'missing')).toBe(false);
    expect(
      updateSite(initial, 'primary', { status: 'planned' }).sites[0].status,
    ).toBe('planned');
    const placed = updateSite(initial, 'primary', { body_ref: '1' });
    expect(isProtectedPrimary(placed, 'primary')).toBe(true);
    expect(initial.sites[0].body_ref).toBeNull();
  });

  it('allows replacement of a completed unplaced primary and protects it once placed', () => {
    const initial = plan(
      site('primary', { status: 'complete', body_ref: null }),
    );
    const replacement = plan(site('replacement'));
    expect(replacePlan(initial, replacement)).toBe(replacement);
    expect(replacePlan(initial, plan()).sites).toEqual([]);
    const placed = updateSite(initial, 'primary', { body_ref: '1' });
    expect(() => replacePlan(placed, replacement)).toThrow('RB-0008');
    expect(() => replacePlan(placed, initial)).toThrow('RB-0008');
  });

  it('blocks adding or inserting above the 300-site API limit', () => {
    const full = plan(
      ...Array.from({ length: 300 }, (_, index) => site(String(index))),
    );
    expect(() => addSite(full, site('overflow'))).toThrow('at most 300');
    expect(() => insertSiteBefore(full, site('overflow'), '1')).toThrow(
      'at most 300',
    );
  });
});

describe('Primary Port locks', () => {
  const cases = [
    {
      status: 'planned',
      body_ref: null,
      facility: false,
      body: false,
      lockedStatus: false,
    },
    {
      status: 'planned',
      body_ref: '1',
      facility: false,
      body: false,
      lockedStatus: false,
    },
    {
      status: 'building',
      body_ref: null,
      facility: true,
      body: false,
      lockedStatus: false,
    },
    {
      status: 'building',
      body_ref: '1',
      facility: true,
      body: true,
      lockedStatus: false,
    },
    {
      status: 'complete',
      body_ref: null,
      facility: true,
      body: false,
      lockedStatus: false,
    },
    {
      status: 'complete',
      body_ref: '1',
      facility: true,
      body: true,
      lockedStatus: true,
    },
  ] as const;

  it.each(cases)(
    'reports every lock for a $status primary at $body_ref',
    ({ status, body_ref, facility, body, lockedStatus }) => {
      expect(primaryPortLocks(site('primary', { status, body_ref }))).toEqual({
        remove: true,
        facility,
        body,
        status: lockedStatus,
        allowedStatuses: ['planned', 'building', 'complete'],
      });
    },
  );

  it.each(cases)(
    'enforces field locks for a $status primary at $body_ref and always allows its name',
    ({ status, body_ref, facility, body, lockedStatus }) => {
      const initial = plan(site('primary', { status, body_ref }));
      const original = structuredClone(initial);
      const fields = [
        { patch: { facility_id: 'coriolis' }, locked: facility },
        { patch: { slot_kind: 'surface' as const }, locked: facility },
        {
          patch: { facility_id: 'surface-port', slot_kind: 'surface' as const },
          locked: facility,
        },
        { patch: { body_ref: 'other' }, locked: body },
        { patch: { body_ref: null }, locked: body },
      ];
      for (const { patch, locked } of fields) {
        if (locked)
          expect(() => updateSite(initial, 'primary', patch)).toThrow(
            PRIMARY_PORT_PROTECTION,
          );
        else
          expect(updateSite(initial, 'primary', patch).sites[0]).toMatchObject(
            patch,
          );
      }
      for (const nextStatus of [
        'planned',
        'building',
        'complete',
        'demolished',
      ] as const) {
        if (nextStatus === 'demolished')
          expect(() =>
            updateSite(initial, 'primary', { status: nextStatus }),
          ).toThrow("The Primary Port can't be demolished (RB-0008).");
        else if (lockedStatus && nextStatus !== status)
          expect(() =>
            updateSite(initial, 'primary', { status: nextStatus }),
          ).toThrow(PRIMARY_PORT_PROTECTION);
        else
          expect(
            updateSite(initial, 'primary', { status: nextStatus }).sites[0]
              .status,
          ).toBe(nextStatus);
      }
      expect(
        updateSite(initial, 'primary', {
          name: 'Home',
          status,
          body_ref,
          facility_id: 'outpost',
          slot_kind: 'orbital',
        }).sites[0].name,
      ).toBe('Home');
      expect(initial).toEqual(original);
    },
  );

  it.each(['building', 'complete'] as const)(
    'allows an Unplaced %s primary to record its body exactly once',
    (status) => {
      const initial = plan(site('primary', { status, body_ref: null }));
      const placed = updateSite(initial, 'primary', { body_ref: '1' });
      expect(placed.sites[0].body_ref).toBe('1');
      for (const body_ref of [null, 'other'])
        expect(() => updateSite(placed, 'primary', { body_ref })).toThrow(
          PRIMARY_PORT_PROTECTION,
        );
      expect(initial.sites[0].body_ref).toBeNull();
    },
  );

  it('allows planned → building → complete and then locks the placed primary status', () => {
    const planned = plan(site('primary'));
    const building = updateSite(planned, 'primary', { status: 'building' });
    expect(building.sites[0].status).toBe('building');
    const complete = updateSite(building, 'primary', { status: 'complete' });
    expect(complete.sites[0].status).toBe('complete');
    expect(() =>
      updateSite(complete, 'primary', { status: 'building' }),
    ).toThrow(PRIMARY_PORT_PROTECTION);
  });

  it('allows naming a legacy demolished primary and recovering its status, but never setting demolished', () => {
    const initial = importPlan(
      exportPlan(plan(site('primary', { status: 'demolished' }))),
      '42',
    );
    expect(updateSite(initial, 'primary', { name: 'Home' }).sites[0].name).toBe(
      'Home',
    );
    expect(() =>
      updateSite(initial, 'primary', { status: 'demolished' }),
    ).toThrow("The Primary Port can't be demolished (RB-0008).");
    for (const status of ['planned', 'building', 'complete'] as const)
      expect(updateSite(initial, 'primary', { status }).sites[0].status).toBe(
        status,
      );
    expect(initial.sites[0]).toMatchObject({ name: '', status: 'demolished' });
  });

  it.each(cases)(
    'abandons a $status primary at $body_ref only before it is complete and placed, keeping slots',
    ({ status, body_ref, lockedStatus }) => {
      const initial = setSlotCount(
        plan(site('primary', { status, body_ref }), site('next')),
        '1',
        'orbital',
        5,
      );
      const original = structuredClone(initial);
      if (lockedStatus)
        expect(() => abandonPlan(initial)).toThrow(PRIMARY_PORT_PROTECTION);
      else {
        const abandoned = abandonPlan(initial);
        expect(abandoned).toEqual({ ...initial, sites: [] });
        expect(abandoned.slots).toBe(initial.slots);
      }
      expect(initial).toEqual(original);
    },
  );

  it('keeps an empty plan and its slots when abandoned', () => {
    const initial = setSlotCount(plan(), '1', 'surface', 3);
    expect(abandonPlan(initial)).toEqual(initial);
    expect(abandonPlan(initial).slots).toBe(initial.slots);
  });
});

describe('plan JSON exchange', () => {
  it('imports a pre-P5b export unchanged through re-export and the browser persistence codec', () => {
    const original = {
      system_id64: preP5bExport.system_id64,
      sites: preP5bExport.sites,
      slots: preP5bExport.slots,
    };
    const imported = importPlan(JSON.stringify(preP5bExport), '42');
    expect(imported).toEqual(original);
    const exported = exportPlan(imported);
    // DRAFT belongs to the download action/filename; the V1 payload stays exact.
    expect(JSON.parse(exported)).toEqual(preP5bExport);
    expect(importPlan(exported, '42')).toEqual(original);

    const record = plannerToRecord(imported);
    const restored = colonyProjectsCodec.decode(
      colonyProjectsCodec.encode({
        version: 3,
        state: { projects: { [record.id]: record } },
      }),
    );
    expect(restored.ok).toBe(true);
    if (!restored.ok) throw new Error('Expected imported plan to hydrate');
    expect(recordToPlanner(restored.value.state.projects[record.id])).toEqual(
      original,
    );
  });

  it('exports the versioned planner-only shape and imports it without loss', () => {
    const original = setSlotCount(plan(site('primary')), '1', 'orbital', 3);
    const text = exportPlan(original);
    expect(JSON.parse(text)).toEqual({
      schema: 'cpe_plan_export_v1',
      ...original,
    });
    expect(importPlan(text, '42')).toEqual(original);
  });

  it('requires explicit confirmation for another system and adopts the current system after confirmation', () => {
    const text = exportPlan(plan(site('primary')));
    expect(() => importPlan(text, '43')).toThrow('another system');
    expect(
      importPlan(text, '43', { confirmOtherSystem: true }).system_id64,
    ).toBe('43');
  });

  it('lets a completed primary imported from another system be placed, then protects its location and status', () => {
    const original = plan(
      site('primary', { status: 'complete', body_ref: 'foreign-body' }),
    );
    const destinationBodies = { ...bodies(), system_id64: '43' };
    const imported = unplaceMissingLocations(
      importPlan(exportPlan(original), '43', { confirmOtherSystem: true }),
      destinationBodies,
    );
    const current = replacePlan(emptyPlan('43'), imported);
    expect(current.sites[0]).toMatchObject({
      status: 'complete',
      body_ref: null,
    });
    expect(isProtectedPrimary(current, 'primary')).toBe(false);
    const placed = updateSite(current, 'primary', { body_ref: '1' });
    expect(placed.sites[0].body_ref).toBe('1');
    expect(isProtectedPrimary(placed, 'primary')).toBe(true);
    for (const patch of [
      { body_ref: null },
      { body_ref: '4' },
      { status: 'planned' as const },
    ])
      expect(() => updateSite(placed, 'primary', patch)).toThrow('RB-0008');
    expect(original.sites[0].body_ref).toBe('foreign-body');
    expect(current.sites[0].body_ref).toBeNull();
  });

  it('rejects malformed, oversized, duplicate, out-of-range and incorrectly versioned imports', () => {
    const exported = JSON.parse(exportPlan(plan(site('primary'))));
    for (const value of [
      { ...exported, schema: 'v2' },
      { ...exported, system_id64: 42 },
      { ...exported, sites: [site('same'), site('same')] },
      { ...exported, sites: [{ ...site('bad-status'), status: ['planned'] }] },
      { ...exported, sites: [site('long', { name: 'x'.repeat(65) })] },
      { ...exported, sites: [site('x'.repeat(65))] },
      { ...exported, sites: [site('long', { facility_id: 'x'.repeat(65) })] },
      { ...exported, sites: [site('long', { body_ref: 'x'.repeat(65) })] },
      {
        ...exported,
        sites: Array.from({ length: 301 }, (_, index) => site(String(index))),
      },
      {
        ...exported,
        slots: { '1': { orbital_slots: 65, surface_slots: null } },
      },
      { ...exported, slots: null },
      {
        ...exported,
        slots: {
          ['x'.repeat(65)]: { orbital_slots: null, surface_slots: null },
        },
      },
    ])
      expect(() => importPlan(JSON.stringify(value), '42')).toThrow();
    expect(() => importPlan('{broken', '42')).toThrow('valid JSON');
    expect(() => importPlan(' '.repeat(MAX_IMPORT_BYTES + 1), '42')).toThrow(
      '2 MB',
    );
    expect(() =>
      importPlan('é'.repeat(MAX_IMPORT_BYTES / 2 + 1), '42'),
    ).toThrow('2 MB');
  });
});
