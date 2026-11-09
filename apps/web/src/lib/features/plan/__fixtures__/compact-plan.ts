import type { PlanBodiesV1, PlanEvaluationV1, PlanFacilityV1 } from '$lib/api';
import type { PlannerModel, PlannerSite } from '../model';

/** Synthetic layout fixture; its evaluation values are presentation examples. */
export function compactPlanFixture(bodyCount = 40, siteCount = 80) {
  const systemName = 'Compact Test System';
  const facilities: PlanFacilityV1[] = [
    {
      id: 'primary-port',
      name: 'Primary Outpost',
      category: 'port',
      tier: 1,
      allowed_location: 'orbital',
      economy: null,
      is_port: true,
      escalating_port_tier: null,
      yellow_cp_cost: 0,
      green_cp_cost: 0,
      yellow_cp_generated: 0,
      green_cp_generated: 0,
      prerequisites: [],
      rule_ids: [],
    },
    {
      id: 'installation',
      name: 'Research Installation',
      category: 'installation',
      tier: 2,
      allowed_location: 'orbital, surface',
      economy: 'High Tech',
      is_port: false,
      escalating_port_tier: null,
      yellow_cp_cost: 3,
      green_cp_cost: 0,
      yellow_cp_generated: 1,
      green_cp_generated: 0,
      prerequisites: [],
      rule_ids: [],
    },
  ];
  const bodies: PlanBodiesV1 = {
    system_id64: '42',
    generation_id: 'compact-layout-test',
    publication_sequence: '1',
    bodies: Array.from({ length: bodyCount }, (_, index) => ({
      body_id: String(index + 1),
      name: `${systemName} A${index ? ` ${index}` : ''}`,
      direct_parent_body_id: index ? '1' : null,
      body_type: index ? 'planet' : 'star',
      is_main_star: index === 0,
      is_landable: index > 0,
      radius_km: index ? 1200 + index * 150 : 400000,
      surface_gravity_g: index ? 0.5 : null,
      surface_temperature_k: index ? 200 : null,
      atmosphere: index ? 'Hot thick Argon-rich' : null,
      terraforming_state: index ? 'Candidate for terraforming' : null,
      volcanism: index ? 'Major Silicate Vapour Geysers' : null,
      distance_from_arrival_ls: index * 150,
      has_rings: index > 0,
      geological_signals: index ? 2 : 0,
      biological_signals: index ? 1 : 0,
      signals_complete: true,
      lifecycle_state: 'ACTIVE',
    })),
    belts: ['Metal Rich', 'Rocky'].map((ring_type, index) => ({
      belt_id: String(index + 1),
      name: `${systemName} A ${index ? 'B' : 'A'} Belt`,
      parent_body_id: '1',
      ring_type,
      reserve: 'Pristine',
      mass_mt: null,
      inner_radius_km: null,
      outer_radius_km: null,
      lifecycle_state: 'ACTIVE',
    })),
  };
  const statuses: PlannerSite['status'][] = [
    'complete',
    'building',
    'planned',
    'demolished',
  ];
  const model: PlannerModel = {
    system_id64: '42',
    sites: Array.from({ length: siteCount }, (_, index) => {
      const bodyIndex = Math.floor(index / 2) % bodyCount;
      return {
        schema: 'cpe_site_v1',
        site_id: `site-${index + 1}`,
        name: `Colony site ${index + 1}`,
        facility_id: facilities[index ? 1 : 0].id,
        body_ref: String(bodyIndex + 1),
        slot_kind: bodyIndex && index % 2 ? 'surface' : 'orbital',
        status: statuses[index % statuses.length],
      };
    }),
    slots: Object.fromEntries(
      bodies.bodies.map((body, index) => [
        body.body_id,
        {
          orbital_slots: index % 3 ? 2 : null,
          surface_slots: index ? 3 : null,
        },
      ]),
    ),
  };
  const results = ['OK', 'BLOCKED', 'NEEDS_INFO'] as const;
  const evaluation: PlanEvaluationV1 = {
    system_id64: '42',
    rulebook_release: 'compact-layout-test',
    now: { t2: 17, t3: 4 },
    after_plan: { t2: 23, t3: 6 },
    next_port_cost: { t2: 3, t3: 6 },
    economies: { now: [], after_plan: [], notes: [] },
    steps: model.sites.map((site, index) => ({
      site_id: site.site_id,
      facility_id: site.facility_id,
      status: site.status,
      view: 'after_plan',
      result: results[index % results.length],
      counted_port_number: index % 5 === 0 ? index + 1 : null,
      cost_t2: 3,
      cost_t3: 0,
      reward_t2: 1,
      reward_t3: 0,
      t2_before: 23,
      t3_before: 6,
      t2_after: 21,
      t3_after: 6,
      reasons: [],
      rule_ids: [],
      payers: [],
    })),
    plan_checks: [],
    summary: { OK: 0, BLOCKED: 0, NEEDS_INFO: 0, COUNTED: 0, IGNORED: 0 },
    explanations: {},
  };
  for (const step of evaluation.steps) evaluation.summary[step.result] += 1;
  return { systemName, bodies, model, facilities, evaluation };
}
