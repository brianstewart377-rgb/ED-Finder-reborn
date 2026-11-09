import { describe, expect, it } from 'vitest';
import type { BodyGroup } from './buildPlanLayout';
import { buildColonyRoleSummaryForGroup } from './colonyRoleHints';

describe('colonyRoleHintUtils', () => {
  it('derives conservative confidence labels from placement concentration and ports', () => {
    const group: BodyGroup = {
      key: '1',
      body: { id: 1, body_type: 'Planet', subtype: 'High metal content world' },
      placements: [
        {
          index: 0,
          bodyId: '1',
          hasUnknownBody: false,
          placement: { facility_template_id: 'port', local_body_id: '1', is_primary_port: true, build_order: 1 },
          template: {
            id: 'port',
            name: 'Orbital Port',
            category: 'port',
            tier: 3,
            economy: 'Industrial',
            is_port: true,
            is_support_facility: false,
            allowed_location: 'orbital',
            pad_size: 'large',
            confidence: 'confirmed',
            notes: null,
            yellow_cp_generated: 0,
            green_cp_generated: 0,
            yellow_cp_cost: 0,
            green_cp_cost: 0,
          },
        },
      ],
    };

    const summary = buildColonyRoleSummaryForGroup(group);

    expect(summary.confidence).toBe('strong');
    expect(summary.hints.map((hint) => hint.compactLabel)).toContain('Main Station Candidate');
    expect(summary.reasoning).toMatch(/Possible main-station body/i);
  });

  it('surfaces role conflicts as advisory overlap instead of errors', () => {
    const group: BodyGroup = {
      key: '2',
      body: { id: 2, body_type: 'Planet', subtype: 'Water world', is_water_world: true },
      placements: [
        {
          index: 0,
          bodyId: '2',
          hasUnknownBody: false,
          placement: { facility_template_id: 'refinery', local_body_id: '2', build_order: 1 },
          template: {
            id: 'refinery',
            name: 'Refinery Hub',
            category: 'support',
            tier: 1,
            economy: 'Refinery',
            is_port: false,
            is_support_facility: true,
            allowed_location: 'surface',
            pad_size: null,
            confidence: 'confirmed',
            notes: null,
            yellow_cp_generated: 0,
            green_cp_generated: 0,
            yellow_cp_cost: 0,
            green_cp_cost: 0,
          },
        },
      ],
    };

    const summary = buildColonyRoleSummaryForGroup(group);

    expect(summary.hints.map((hint) => hint.compactLabel)).toEqual(
      expect.arrayContaining(['Industrial Candidate', 'Refinery Candidate', 'Tourism Pressure']),
    );
    expect(summary.conflicts).toContain('Industrial + tourism pressure overlap.');
  });

  it('lowers sparse metadata confidence without blocking role hints', () => {
    const group: BodyGroup = {
      key: '9',
      body: { id: 9 },
      placements: [
        {
          index: 0,
          bodyId: '9',
          hasUnknownBody: false,
          placement: { facility_template_id: 'port', local_body_id: '9', is_primary_port: true, build_order: 1 },
          template: {
            id: 'port',
            name: 'Orbital Port',
            category: 'port',
            tier: 3,
            economy: null,
            is_port: true,
            is_support_facility: false,
            allowed_location: 'orbital',
            pad_size: 'large',
            confidence: 'confirmed',
            notes: null,
            yellow_cp_generated: 0,
            green_cp_generated: 0,
            yellow_cp_cost: 0,
            green_cp_cost: 0,
          },
        },
      ],
    };

    const summary = buildColonyRoleSummaryForGroup(group);

    expect(summary.confidence).toBe('tentative');
    expect(summary.warnings).toContain('Sparse metadata lowers confidence.');
    expect(summary.hints.map((hint) => hint.compactLabel)).toContain('Sparse Metadata');
  });
});
