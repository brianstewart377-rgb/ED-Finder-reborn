import { describe, expect, it } from 'vitest';
import type { PlanBodiesV1, PlanFacilityV1, PlanSystemBodyV1 } from '$lib/api';
import {
  exportPlan,
  importPlan,
  isPlannerSite,
  MAX_SITES,
  rejectInvalidLocations,
} from './model';
import { resolvePlanLocations } from './locations';
import { ravenToPlannerModel } from './raven-apply';
import { parseRavenLayout, type RavenImportResult } from './raven-import';

function body(
  body_id: string,
  name: string,
  body_type = 'planet',
): PlanSystemBodyV1 {
  return {
    body_id,
    name,
    direct_parent_body_id: null,
    body_type,
    is_main_star: body_type === 'star',
    is_landable: body_type === 'planet',
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

function bodies(): PlanBodiesV1 {
  return {
    system_id64: '42',
    generation_id: 'synthetic',
    publication_sequence: '1',
    bodies: [
      body('1', 'Amber Lantern A', 'star'),
      body('2', 'Amber Lantern A 1'),
      body('3', 'Amber Lantern AB', 'barycentre'),
      ...[1, 2, 3, 4].map((cluster) =>
        body(
          `${cluster + 10}`,
          `Amber Lantern A Belt Cluster ${cluster}`,
          'belt cluster',
        ),
      ),
    ],
    belts: [
      {
        belt_id: 'a',
        name: 'Amber Lantern A Belt',
        parent_body_id: '1',
        ring_type: 'Rocky',
        reserve: 'Pristine',
        mass_mt: null,
        inner_radius_km: null,
        outer_radius_km: null,
        lifecycle_state: 'ACTIVE',
      },
    ],
  };
}

function site(
  overrides: Partial<RavenImportResult['sites'][number]> = {},
): RavenImportResult['sites'][number] {
  return {
    raven_site_id: 'copper-dock',
    raven_build_type: 'vulcan',
    name: 'Copper Dock',
    facility_id: 'orbital_outpost_industrial',
    slot_kind: 'orbital',
    status: 'planned',
    raven_body_name: 'Amber Lantern A 1',
    ...overrides,
  };
}

function layout(overrides: Partial<RavenImportResult> = {}): RavenImportResult {
  return {
    ok: true,
    system: { id64: '42', name: 'Amber Lantern' },
    errors: [],
    warnings: [],
    sites: [site()],
    slots: [],
    ...overrides,
  };
}

function codes(result: ReturnType<typeof ravenToPlannerModel>) {
  return result.warnings.map(({ code }) => code);
}

describe('ravenToPlannerModel', () => {
  it('matches an exact body name and preserves the site data', () => {
    const result = ravenToPlannerModel(layout(), bodies());
    expect(result.model.sites).toEqual([
      {
        schema: 'cpe_site_v1',
        site_id: 'raven:copper-dock',
        facility_id: 'orbital_outpost_industrial',
        slot_kind: 'orbital',
        status: 'planned',
        name: 'Copper Dock',
        body_ref: '2',
      },
    ]);
    expect(result.warnings).toEqual([]);
  });

  it('matches body names case-insensitively after trimming', () => {
    const result = ravenToPlannerModel(
      layout({ sites: [site({ raven_body_name: ' AMBER lantern a 1 ' })] }),
      bodies(),
    );
    expect(result.model.sites[0].body_ref).toBe('2');
    expect(result.warnings).toEqual([]);
  });

  it.each(['Amber Lantern A 999', null])(
    'keeps unmatched body %s Unplaced with matcher warnings',
    (raven_body_name) => {
      const result = ravenToPlannerModel(
        layout({ sites: [site({ raven_body_name })] }),
        bodies(),
      );
      expect(result.model.sites[0].body_ref).toBeNull();
      expect(codes(result)).toContain('unmatched_body');
    },
  );

  it('keeps ambiguous body names Unplaced', () => {
    const data = bodies();
    data.bodies.push(body('99', ' AMBER LANTERN A 1 '));
    const result = ravenToPlannerModel(layout(), data);
    expect(result.model.sites[0].body_ref).toBeNull();
    expect(codes(result)).toContain('ambiguous_body');
  });

  it.each([
    ['duplicate belt names', false],
    ['duplicate belt names', true],
    ['overlapping belt aliases', false],
    ['overlapping belt aliases', true],
  ])(
    'keeps %s Unplaced and drops their slots without falling through to body matching (case/space variant: %s)',
    (scenario, variant) => {
      const data = bodies();
      const raven_body_name =
        scenario === 'duplicate belt names'
          ? 'Amber Lantern A Belt'
          : 'Amber Lantern A Belt Cluster 1';
      if (scenario === 'duplicate belt names') {
        data.bodies = data.bodies.slice(0, 3);
        // A normal exact body match must not override explicit belt ambiguity.
        data.bodies.push(body('99', raven_body_name));
      }
      data.belts.push({
        ...data.belts[0],
        belt_id: 'other',
        name: variant ? ` ${raven_body_name.toUpperCase()} ` : raven_body_name,
      });
      const source = layout({
        sites: [site({ raven_body_name }), site({ raven_site_id: 'ordinary' })],
        slots: [
          { raven_body_name, orbital_slots: 7, surface_slots: null },
          {
            raven_body_name: 'Amber Lantern A 1',
            orbital_slots: 3,
            surface_slots: 2,
          },
        ],
      });
      const result = ravenToPlannerModel(source, data);
      expect(result.model.sites.map(({ body_ref }) => body_ref)).toEqual([
        null,
        '2',
      ]);
      expect(result.model.slots).toEqual({
        '2': { orbital_slots: 3, surface_slots: 2 },
      });
      expect(codes(result)).toEqual([
        'ambiguous_body',
        'ambiguous_body',
        'slots_dropped',
      ]);
      expect(
        result.warnings.every(({ detail }) => detail.includes(raven_body_name)),
      ).toBe(true);
      expect(() => rejectInvalidLocations(result.model, data)).not.toThrow();
      expect(
        ravenToPlannerModel(source, {
          ...data,
          belts: data.belts.toReversed(),
        }),
      ).toEqual(result);
    },
  );

  it.each(['barycentre', 'barycenter'])(
    'keeps a %s Unplaced with hidden_body',
    (body_type) => {
      const data = bodies();
      data.bodies[2].body_type = body_type;
      const result = ravenToPlannerModel(
        layout({ sites: [site({ raven_body_name: 'Amber Lantern AB' })] }),
        data,
      );
      expect(result.model.sites[0].body_ref).toBeNull();
      expect(codes(result)).toEqual(['hidden_body']);
    },
  );

  it('keeps retired bodies Unplaced and passes current-location validation', () => {
    const data = bodies();
    data.bodies[1].lifecycle_state = 'RETIRED';
    const result = ravenToPlannerModel(layout(), data);
    expect(result.model.sites[0].body_ref).toBeNull();
    expect(codes(result)).toEqual(['hidden_body']);
    expect(() => rejectInvalidLocations(result.model, data)).not.toThrow();
  });

  it.each([
    ['Amber Lantern A Belt Cluster 1', false],
    ['Amber Lantern A Belt Cluster 3', true],
    ['Amber Lantern A Belt Cluster 999', true],
    ['Amber Lantern A Belt', false],
    ['AMBER LANTERN A BELT CLUSTER 2', true],
    ['aMbEr LaNtErN a BeLt cLuStEr 3', true],
    [' AMBER LANTERN A BELT CLUSTER 2 ', true],
    [' aMbEr LaNtErN a BeLt cLuStEr 3 ', true],
    [' Amber Lantern A Belt Cluster 2 ', true],
    ['AMBER LANTERN A BELT', false],
    [' AMBER LANTERN A BELT ', false],
    [' Amber Lantern A Belt ', false],
  ])('maps %s to the single belt location', (raven_body_name, moved) => {
    const result = ravenToPlannerModel(
      layout({
        sites: [site({ raven_body_name })],
        slots: [{ raven_body_name, orbital_slots: 7, surface_slots: 0 }],
      }),
      bodies(),
    );
    expect(result.model.sites[0].body_ref).toBe('11');
    expect(result.model.slots).toEqual({
      '11': { orbital_slots: 7, surface_slots: 0 },
    });
    expect(codes(result)).toEqual(
      moved ? ['belt_cluster_moved', 'belt_cluster_moved'] : [],
    );
    if (moved) {
      expect(result.warnings[0].detail).toContain(raven_body_name);
      expect(result.warnings[0].detail).toContain('Amber Lantern A Belt');
    }
  });

  it.each(['2x', '2.0', '+2', '-2', '2 3', ''])(
    'rejects a non-digit cluster suffix %j after normalising the name',
    (suffix) => {
      const raven_body_name = ` AMBER LANTERN A BELT CLUSTER ${suffix} `;
      const result = ravenToPlannerModel(
        layout({
          sites: [site({ raven_body_name })],
          slots: [{ raven_body_name, orbital_slots: 7, surface_slots: 0 }],
        }),
        bodies(),
      );
      expect(result.model.sites[0].body_ref).toBeNull();
      expect(result.model.slots).toEqual({});
      expect(codes(result)).toEqual([
        'unmatched_body',
        'unmatched_body',
        'slots_dropped',
      ]);
    },
  );

  it('maps a Raven cluster to belt:<id> when ED-Finder has no clusters', () => {
    const data = bodies();
    data.bodies = data.bodies.slice(0, 3);
    const result = ravenToPlannerModel(
      layout({
        sites: [site({ raven_body_name: 'Amber Lantern A Belt Cluster 1' })],
      }),
      data,
    );
    expect(result.model.sites[0].body_ref).toBe('belt:a');
    expect(codes(result)).toEqual(['belt_cluster_moved']);
  });

  it('uses the lowest available cluster even when it is not Cluster 1', () => {
    const data = bodies();
    data.bodies = data.bodies.filter((entry) => entry.body_id !== '11');
    const result = ravenToPlannerModel(
      layout({
        sites: [site({ raven_body_name: 'Amber Lantern A Belt Cluster 2' })],
      }),
      data,
    );
    expect(result.model.sites[0].body_ref).toBe('12');
    expect(result.warnings).toEqual([]);
  });

  it('keeps an unassociated hidden belt cluster Unplaced', () => {
    const data = bodies();
    data.bodies.push(
      body('88', 'Amber Lantern C Belt Cluster 1', 'belt cluster'),
    );
    const result = ravenToPlannerModel(
      layout({
        sites: [site({ raven_body_name: 'Amber Lantern C Belt Cluster 1' })],
      }),
      data,
    );
    expect(result.model.sites[0].body_ref).toBeNull();
    expect(codes(result)).toEqual(['hidden_body']);
  });

  it('merges belt slot rows using the lowest numeric cluster regardless of input order', () => {
    const result = ravenToPlannerModel(
      layout({
        slots: [
          {
            raven_body_name: 'Amber Lantern A Belt Cluster 10',
            orbital_slots: 10,
            surface_slots: null,
          },
          {
            raven_body_name: 'Amber Lantern A Belt',
            orbital_slots: 8,
            surface_slots: null,
          },
          {
            raven_body_name: 'Amber Lantern A Belt Cluster 2',
            orbital_slots: 2,
            surface_slots: 0,
          },
        ],
      }),
      bodies(),
    );
    expect(result.model.slots).toEqual({
      '11': { orbital_slots: 2, surface_slots: 0 },
    });
    expect(
      codes(result).filter((code) => code === 'belt_slots_merged'),
    ).toHaveLength(2);
    expect(
      result.warnings
        .filter(({ code }) => code === 'belt_slots_merged')
        .every(({ detail }) => detail.includes('Cluster 2')),
    ).toBe(true);
  });

  it('drops unmatched, ambiguous and hidden slot rows with warnings', () => {
    const data = bodies();
    data.bodies.push(body('99', 'Amber Lantern A 1'));
    const result = ravenToPlannerModel(
      layout({
        sites: [],
        slots: [
          {
            raven_body_name: 'Absent body',
            orbital_slots: 2,
            surface_slots: 0,
          },
          {
            raven_body_name: 'Amber Lantern A 1',
            orbital_slots: 2,
            surface_slots: 0,
          },
          {
            raven_body_name: 'Amber Lantern AB',
            orbital_slots: 2,
            surface_slots: 0,
          },
        ],
      }),
      data,
    );
    expect(result.model.slots).toEqual({});
    expect(codes(result)).toEqual(
      expect.arrayContaining([
        'unmatched_body',
        'ambiguous_body',
        'hidden_body',
      ]),
    );
    expect(
      codes(result).filter((code) => code === 'slots_dropped'),
    ).toHaveLength(3);
  });

  it.each([false, true])(
    'merges mixed exact and case-insensitive belt rows (reversed: %s)',
    (reversed) => {
      const slots = [
        {
          raven_body_name: ' AMBER LANTERN A BELT CLUSTER 1 ',
          orbital_slots: 4,
          surface_slots: null,
        },
        {
          raven_body_name: 'Amber Lantern A Belt Cluster 3',
          orbital_slots: 2,
          surface_slots: null,
        },
      ];
      const result = ravenToPlannerModel(
        layout({ slots: reversed ? slots.toReversed() : slots }),
        bodies(),
      );
      expect(result.model.slots).toEqual({
        '11': { orbital_slots: 4, surface_slots: null },
      });
      expect(
        codes(result).filter((code) => code === 'belt_slots_merged'),
      ).toHaveLength(1);
      expect(() =>
        rejectInvalidLocations(result.model, bodies()),
      ).not.toThrow();
    },
  );

  it('keeps slot bounds and normalises invalid counts to Unknown', () => {
    const result = ravenToPlannerModel(
      layout({
        slots: [
          {
            raven_body_name: 'Amber Lantern A',
            orbital_slots: 0,
            surface_slots: 64,
          },
          {
            raven_body_name: 'Amber Lantern A 1',
            orbital_slots: 65,
            surface_slots: 0.5,
          },
        ],
      }),
      bodies(),
    );
    expect(result.model.slots).toEqual({
      '1': { orbital_slots: 0, surface_slots: 64 },
      '2': { orbital_slots: null, surface_slots: null },
    });
    expect(codes(result)).toEqual(['invalid_slots']);
    expect(importPlan(exportPlan(result.model), '42')).toEqual(result.model);
  });

  it('derives unique bounded ids for duplicate and invalid Raven site ids', () => {
    const result = ravenToPlannerModel(
      layout({
        sites: [
          site(),
          site(),
          site({ raven_site_id: 'copper-dock:2' }),
          site({ raven_site_id: 'x'.repeat(256) }),
          site({ raven_site_id: '' }),
        ],
      }),
      bodies(),
    );
    const ids = result.model.sites.map(({ site_id }) => site_id);
    expect(new Set(ids).size).toBe(5);
    expect(result.model.sites.every(isPlannerSite)).toBe(true);
    expect(
      codes(result).filter((code) => code === 'site_id_changed'),
    ).toHaveLength(4);
  });

  it('keeps the first MAX_SITES valid sites in Raven order and warns for excess', () => {
    // Exercise the mapping boundary independently of the parser's own limit.
    const result = ravenToPlannerModel(
      layout({
        sites: Array.from({ length: 301 }, (_, index) =>
          site({ raven_site_id: `site-${index}`, name: `Dock ${index}` }),
        ),
      }),
      bodies(),
    );
    expect(result.model.sites).toHaveLength(MAX_SITES);
    expect(result.model.sites.map(({ name }) => name)).toEqual(
      Array.from({ length: MAX_SITES }, (_, index) => `Dock ${index}`),
    );
    expect(codes(result)).toEqual(['too_many_sites']);
  });

  it('preserves all statuses, slot kinds, facilities and the parser warnings', () => {
    const warning = {
      code: 'unknown_build_type',
      detail: 'Synthetic skipped facility.',
    };
    const source = layout({
      warnings: [warning],
      sites: ['planned', 'building', 'complete', 'demolished'].map(
        (status, index) =>
          site({
            raven_site_id: `${index}`,
            status: status as RavenImportResult['sites'][number]['status'],
            slot_kind: index % 2 ? 'surface' : 'orbital',
          }),
      ),
    });
    const result = ravenToPlannerModel(source, bodies());
    expect(result.warnings).toEqual([warning]);
    expect(
      result.model.sites.map(({ status, slot_kind, facility_id, name }) => ({
        status,
        slot_kind,
        facility_id,
        name,
      })),
    ).toEqual(
      source.sites.map(({ status, slot_kind, facility_id, name }) => ({
        status,
        slot_kind,
        facility_id,
        name,
      })),
    );
  });

  it('keeps a mismatched facility on its location and warns using the supplied catalogue', () => {
    const source = layout({
      sites: [
        site({
          facility_id: 'surface_hub_civilian',
          slot_kind: 'surface',
          raven_body_name: 'Amber Lantern A Belt',
        }),
      ],
    });
    const facilities = [
      {
        id: 'surface_hub_civilian',
        name: 'Civilian hub',
        allowed_location: 'surface',
      } as PlanFacilityV1,
    ];
    const result = ravenToPlannerModel(source, bodies(), facilities);
    expect(result.model.sites[0].body_ref).toBe('11');
    expect(codes(result)).toEqual(['facility_location_mismatch']);
    expect(result.warnings[0].detail).toContain('Copper Dock');
    expect(result.warnings[0].detail).toContain('Civilian hub');
    expect(ravenToPlannerModel(source, bodies()).warnings).toEqual([]);
  });

  it('warns and skips a facility that cannot satisfy the planner model', () => {
    const result = ravenToPlannerModel(
      layout({
        sites: [
          site({ facility_id: 'x'.repeat(65) }),
          site({ raven_site_id: 'valid' }),
        ],
      }),
      bodies(),
    );
    expect(result.model.sites).toHaveLength(1);
    expect(codes(result)).toEqual(['invalid_site']);
    expect(result.warnings[0].detail).toContain('Copper Dock');
    expect(result.warnings[0].detail).toContain('x'.repeat(65));
  });

  it('keeps Unicode names within model bounds without splitting a character', () => {
    const parsed = parseRavenLayout({
      v: 6,
      id64: '42',
      name: 'Amber Lantern',
      bodies: [{ num: 1, name: 'Amber Lantern A 1' }],
      sites: [
        {
          id: 'unicode',
          buildType: 'vulcan',
          status: 'plan',
          name: '🪐'.repeat(64),
          bodyNum: 1,
        },
      ],
    });
    const result = ravenToPlannerModel(parsed, bodies());
    expect(result.model.sites[0].name).toBe('🪐'.repeat(32));
    expect(isPlannerSite(result.model.sites[0])).toBe(true);
    expect(codes(result)).toContain('name_truncated');
  });

  it('produces only valid sites and current locations and is deterministic without input mutation', () => {
    const data = bodies();
    const source = layout({
      sites: [
        site(),
        site({ raven_body_name: 'Amber Lantern A Belt Cluster 4' }),
        site({ raven_body_name: 'Absent body' }),
        site({ raven_body_name: 'Amber Lantern AB' }),
      ],
      slots: [
        {
          raven_body_name: 'Amber Lantern A Belt Cluster 2',
          orbital_slots: 3,
          surface_slots: null,
        },
      ],
    });
    const before = structuredClone({ source, data });
    const result = ravenToPlannerModel(source, data);
    expect(ravenToPlannerModel(source, data)).toEqual(result);
    expect({ source, data }).toEqual(before);
    expect(result.model.sites.every(isPlannerSite)).toBe(true);
    expect(() => rejectInvalidLocations(result.model, data)).not.toThrow();
    const allowed = new Set(
      resolvePlanLocations(data).map(({ body_ref }) => body_ref),
    );
    expect(
      result.model.sites.every(
        ({ body_ref }) => body_ref === null || allowed.has(body_ref),
      ),
    ).toBe(true);
    expect(
      Object.keys(result.model.slots).every((ref) => allowed.has(ref)),
    ).toBe(true);
    expect(importPlan(exportPlan(result.model), '42')).toEqual(result.model);
  });

  it('refuses unsuccessful results and another system', () => {
    expect(() => ravenToPlannerModel(layout({ ok: false }), bodies())).toThrow(
      'could not be imported',
    );
    expect(() =>
      ravenToPlannerModel(
        layout({ system: { id64: '43', name: 'Blue Lantern' } }),
        bodies(),
      ),
    ).toThrow('different system');
  });
});
