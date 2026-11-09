import { describe, expect, it } from 'vitest';
import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';
import { groupPlacementsByBody } from './buildPlanLayout';
import { buildStrategicTopologyGuidanceForGroup } from './strategicTopologyGuidance';

const templates: FacilityTemplate[] = [
  {
    id: 'orbital_port',
    name: 'Orbital Port',
    category: 'port',
    tier: 3,
    economy: 'Industrial',
    is_port: true,
    is_support_facility: false,
    allowed_location: 'orbital',
    pad_size: 'large',
    confidence: 'observed',
    notes: null,
    yellow_cp_generated: 0,
    green_cp_generated: 0,
    yellow_cp_cost: 20,
    green_cp_cost: 40,
  },
  {
    id: 'tourism_outpost',
    name: 'Tourism Outpost',
    category: 'support',
    tier: 1,
    economy: 'Tourism',
    is_port: false,
    is_support_facility: true,
    allowed_location: 'orbital',
    pad_size: 'medium',
    confidence: 'observed',
    notes: null,
    yellow_cp_generated: 4,
    green_cp_generated: 1,
    yellow_cp_cost: 0,
    green_cp_cost: 0,
  },
  {
    id: 'surface_hub',
    name: 'Surface Hub',
    category: 'support',
    tier: 1,
    economy: 'Extraction',
    is_port: false,
    is_support_facility: true,
    allowed_location: 'surface',
    pad_size: 'small',
    confidence: 'estimated',
    notes: null,
    yellow_cp_generated: 5,
    green_cp_generated: 1,
    yellow_cp_cost: 0,
    green_cp_cost: 0,
  },
];

const bodies = [
  { id: 1, name: 'A 1', body_type: 'Planet', subtype: 'High metal content world', is_landable: true },
  { id: 2, name: 'A 2', body_type: 'Planet', subtype: 'Water world', is_water_world: true },
  { id: 3, name: 'A 3' },
] as SystemBody[];

function groupsFor(placements: SimulateBuildPlacement[]) {
  return groupPlacementsByBody(placements, templates, bodies);
}

describe('strategicTopologyGuidanceUtils', () => {
  it('labels a planned primary or major port as a main station candidate without changing scoring', () => {
    const groups = groupsFor([
      { facility_template_id: 'orbital_port', local_body_id: '1', is_primary_port: true, build_order: 1 },
    ]);

    const guidance = buildStrategicTopologyGuidanceForGroup(groups[0], groups);
    const text = guidance.map((item) => item.text);

    expect(text).toContain('Main station candidate: current plan places a primary or major port here.');
    expect(text).toContain('Primary-port flag unknown: check Architect Mode before final station placement.');
    expect(text).toContain('Consider outpost on inconvenient primary-port slot and main station elsewhere.');
    expect(guidance.some((item) => item.id === 'strategic-main-station-candidate' && item.severity === 'advisory')).toBe(true);
  });

  it('labels support-focused bodies and likely tourism/agriculture pressure from existing body facts only', () => {
    const groups = groupsFor([
      { facility_template_id: 'orbital_port', local_body_id: '1', is_primary_port: true, build_order: 1 },
      { facility_template_id: 'tourism_outpost', local_body_id: '2', build_order: 2 },
    ]);

    const supportGroup = groups.find((group) => group.key === '2');
    if (!supportGroup) throw new Error('Missing support group');

    const guidance = buildStrategicTopologyGuidanceForGroup(supportGroup, groups);
    const text = guidance.map((item) => item.text);

    expect(text).toContain('Good support body: current plan keeps this body support-focused away from the main station candidate.');
    expect(text).toContain('Likely tourism/agriculture pressure: body context may favour reviewing tourism or agriculture support.');
  });

  it('keeps observed Architect primary-port context from showing the unknown-flag guidance', () => {
    const groups = groupsFor([
      { facility_template_id: 'orbital_port', local_body_id: '1', build_order: 1 },
    ]);

    const guidance = buildStrategicTopologyGuidanceForGroup(groups[0], groups, {
      surveyState: 'observed',
      primaryPortFlag: { state: 'observed', bodyName: 'A 1', slotLabel: 'Orbital slot 1' },
    });

    expect(guidance.map((item) => item.text)).not.toContain('Primary-port flag unknown: check Architect Mode before final station placement.');
    expect(guidance.map((item) => item.text)).toContain('Main station candidate: current plan places a primary or major port here.');
  });

  it('uses caution copy for sparse or unknown bodies without inventing relationships', () => {
    const sparseGroup = groupsFor([
      { facility_template_id: 'surface_hub', local_body_id: '3', build_order: 1 },
    ])[0];
    const unknownGroup = groupsFor([
      { facility_template_id: 'surface_hub', local_body_id: '404', build_order: 1 },
    ])[0];

    expect(buildStrategicTopologyGuidanceForGroup(sparseGroup).map((item) => item.text)).toContain('Sparse metadata: confirm in game.');
    expect(buildStrategicTopologyGuidanceForGroup(unknownGroup).map((item) => item.text)).toContain('Sparse metadata: confirm in game before treating this as a strategic body.');
  });
});
