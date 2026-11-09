import { describe, expect, it } from 'vitest';
import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';
import { groupPlacementsByBody } from './buildPlanLayout';
import { buildLayoutTopologyReadout, topologyPlacementLocationLabel } from './layoutTopology';

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
    id: 'surface_hub',
    name: 'Surface Hub',
    category: 'support',
    tier: 2,
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
  { id: 1, name: 'A 1', is_landable: true, body_type: 'Planet' },
  { id: 2, name: 'A 2', is_landable: false, body_type: 'Planet' },
  { id: 3, name: 'A 3', is_water_world: true, body_type: 'Planet' },
] as SystemBody[];

function groupFor(placements: SimulateBuildPlacement[], key: string) {
  const group = groupPlacementsByBody(placements, templates, bodies).find((item) => item.key === key);
  if (!group) throw new Error(`Missing group ${key}`);
  return group;
}

describe('layoutTopologyUtils', () => {
  it('counts orbital and ground planned placements without inventing slot capacity', () => {
    const group = groupFor([
      { facility_template_id: 'orbital_port', local_body_id: '1', is_primary_port: true, build_order: 1 },
      { facility_template_id: 'surface_hub', local_body_id: '1', build_order: 2 },
    ], '1');

    const readout = buildLayoutTopologyReadout(group);

    expect(readout.bodyLabel).toBe('A 1');
    expect(readout.bodyState).toBe('known');
    expect(readout.orbitalPlanned).toBe(1);
    expect(readout.groundPlanned).toBe(1);
    expect(readout.groundCapabilityLabel).toBe('Surface capability: landable');
    expect(readout.orbitalSlotLabel).toBe('Orbital slots: unknown');
    expect(readout.groundSlotLabel).toBe('Ground slots: unknown');
    expect(readout.primaryPortContextLabel).toBe('Primary-port plan: on this body');
  });

  it('flags non-landable, water-world, unknown body, and unassigned contexts conservatively', () => {
    const nonLandable = buildLayoutTopologyReadout(groupFor([
      { facility_template_id: 'surface_hub', local_body_id: '2', build_order: 1 },
    ], '2'));
    expect(nonLandable.groundCapabilityLabel).toBe('Surface capability: not landable');
    expect(nonLandable.groundCapabilityTone).toBe('warn');

    const waterWorld = buildLayoutTopologyReadout(groupFor([
      { facility_template_id: 'surface_hub', local_body_id: '3', build_order: 1 },
    ], '3'));
    expect(waterWorld.groundCapabilityLabel).toBe('Surface capability: review water world');

    const unknown = buildLayoutTopologyReadout(groupFor([
      { facility_template_id: 'surface_hub', local_body_id: '404', build_order: 1 },
    ], 'unassigned'));
    expect(unknown.bodyState).toBe('unknown');
    expect(unknown.bodyLabel).toBe('Unknown body reference');
    expect(unknown.groundCapabilityLabel).toBe('Surface capability: unknown');

    const unassigned = buildLayoutTopologyReadout(groupFor([
      { facility_template_id: 'surface_hub', local_body_id: null, build_order: 1 },
    ], 'unassigned'));
    expect(unassigned.bodyState).toBe('unassigned');
    expect(unassigned.bodyLabel).toBe('Unassigned / needs body');
  });

  it('renders observed Architect mock context as read-only labels when supplied', () => {
    const readout = buildLayoutTopologyReadout(groupFor([
      { facility_template_id: 'orbital_port', local_body_id: '1', build_order: 1 },
    ], '1'), {
      surveyState: 'observed',
      orbitalSlotCount: 4,
      groundSlotCount: 2,
      primaryPortFlag: { state: 'observed', bodyName: 'A 1', slotLabel: 'Orbital slot 2' },
    });

    expect(readout.architectSurveyLabel).toBe('Architect survey: observed');
    expect(readout.orbitalSlotLabel).toBe('Orbital slots: 4');
    expect(readout.groundSlotLabel).toBe('Ground slots: 2');
    expect(readout.primaryPortContextLabel).toBe('Primary-port flag: observed on A 1 / Orbital slot 2');
  });

  it('formats placement topology labels from existing template location only', () => {
    expect(topologyPlacementLocationLabel(templates[0])).toBe('Topology location: orbital');
    expect(topologyPlacementLocationLabel(undefined)).toBe('Topology location: unknown');
  });
});
