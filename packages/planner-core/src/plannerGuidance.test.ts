import { describe, expect, it } from 'vitest';
import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';
import {
  buildPlannerGuidanceForBody,
  buildPlannerGuidanceForPlacement,
  plannerSeverityForWarning,
} from './plannerGuidance';

const orbitalPort: FacilityTemplate = {
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
};

const surfaceSupport: FacilityTemplate = {
  id: 'surface_support',
  name: 'Surface Support',
  category: 'support',
  tier: 1,
  economy: 'Extraction',
  is_port: false,
  is_support_facility: true,
  allowed_location: 'surface',
  pad_size: 'small',
  confidence: 'estimated',
  notes: null,
  yellow_cp_generated: 8,
  green_cp_generated: 2,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
};

const placement: SimulateBuildPlacement = {
  facility_template_id: 'surface_support',
  local_body_id: '2',
  build_order: 1,
};

describe('plannerGuidanceUtils', () => {
  it('maps warning strings to conservative severity levels', () => {
    expect(plannerSeverityForWarning('May be invalid: surface facility on water world')).toBe('incompatible');
    expect(plannerSeverityForWarning('Needs review: template uses estimated data')).toBe('advisory');
    expect(plannerSeverityForWarning('Data incomplete: body metadata is sparse')).toBe('caution');
    expect(plannerSeverityForWarning('Unknown planner note')).toBe('advisory');
  });

  it('builds high-risk placement guidance without blocking explicit actions', () => {
    const guidance = buildPlannerGuidanceForPlacement({
      placement,
      template: surfaceSupport,
      body: { id: 2, name: 'A 2', is_water_world: true } as SystemBody,
      warnings: [
        'May be invalid: surface facility on water world',
        'Needs review: template uses estimated data',
      ],
    });

    expect(guidance.map((item) => item.severity)).toContain('incompatible');
    expect(guidance.map((item) => item.text)).toContain('Surface structure may be invalid on this body.');
    expect(guidance.map((item) => item.text)).toContain('Estimated template data: review before relying on the plan.');
  });

  it('keeps Architect guidance read-only and informational', () => {
    const guidance = buildPlannerGuidanceForPlacement({
      placement: { facility_template_id: 'orbital_port', local_body_id: '1', is_primary_port: true, build_order: 1 },
      template: orbitalPort,
      body: { id: 1, name: 'A 1', body_type: 'Planet' } as SystemBody,
      warnings: [],
    });

    expect(guidance).toContainEqual(expect.objectContaining({
      severity: 'info',
      text: 'Architect primary-port location should be checked before final major station placement.',
    }));
    expect(guidance).toContainEqual(expect.objectContaining({
      severity: 'advisory',
      text: 'If the flagged primary-port slot is inconvenient, consider an outpost there and place the main station elsewhere.',
    }));
  });

  it('derives body guidance from existing body and placement data only', () => {
    const guidance = buildPlannerGuidanceForBody(
      { id: 2, name: 'A 2', is_water_world: true } as SystemBody,
      [{
        placement: { facility_template_id: 'orbital_port', local_body_id: '2', build_order: 1 },
        template: orbitalPort,
        body: { id: 2, name: 'A 2', is_water_world: true } as SystemBody,
        warnings: [],
      }],
    );

    expect(guidance.map((item) => item.text)).toContain('Water-world orbital planning may favour tourism/agriculture.');
    expect(guidance.map((item) => item.text)).toContain('Architect primary-port location should be checked before final major station placement.');
  });
});
