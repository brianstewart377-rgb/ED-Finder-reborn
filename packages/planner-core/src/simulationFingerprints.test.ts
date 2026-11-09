import { describe, expect, it } from 'vitest';
import type { SimulateBuildPlacement } from '@ed-finder/api-client/types';
import {
  planSnapshotEmissionFingerprint,
  previewInputFingerprint,
  simulationRequestFingerprint,
} from './simulationFingerprints';

const placements: SimulateBuildPlacement[] = [
  { facility_template_id: 'generic_port_alpha', local_body_id: 'body1', is_primary_port: true, build_order: 2 },
  { facility_template_id: 'agri_support_a', local_body_id: 'body1', is_primary_port: false, build_order: 1 },
];

describe('previewInputFingerprint', () => {
  it('includes system id so identical plans in different systems are distinct', () => {
    const first = previewInputFingerprint(123, 'agriculture_terraforming', placements);
    const second = previewInputFingerprint(456, 'agriculture_terraforming', placements);

    expect(first).not.toBe(second);
    expect(JSON.parse(first)).toMatchObject({ system_id64: 123 });
    expect(JSON.parse(second)).toMatchObject({ system_id64: 456 });
  });

  it('uses the same resequenced request envelope for direct and request fingerprints', () => {
    const direct = previewInputFingerprint(123, 'agriculture_terraforming', placements);
    const fromRequest = simulationRequestFingerprint({
      system_id64: 123,
      target_archetype: 'agriculture_terraforming',
      placements,
    });

    expect(fromRequest).toBe(direct);
    expect(JSON.parse(direct)).toEqual({
      system_id64: 123,
      target_archetype: 'agriculture_terraforming',
      placements: [
        {
          facility_template_id: 'generic_port_alpha',
          local_body_id: 'body1',
          is_primary_port: true,
          build_order: 1,
        },
        {
          facility_template_id: 'agri_support_a',
          local_body_id: 'body1',
          is_primary_port: false,
          build_order: 2,
        },
      ],
    });
  });

  it('keeps the plan-snapshot envelope distinct from lane-hint presentation state', () => {
    const fingerprint = planSnapshotEmissionFingerprint(
      placements,
      'agriculture_terraforming',
      {
        candidateId: 'candidate-1',
        label: 'Candidate one',
        placements: [placements[1]],
        placementLaneHints: { 0: 'surface' },
      },
    );

    expect(JSON.parse(fingerprint)).toEqual({
      targetArchetype: 'agriculture_terraforming',
      placements: placements.map((placement) => ({
        facility_template_id: placement.facility_template_id,
        local_body_id: placement.local_body_id,
        is_primary_port: placement.is_primary_port,
        build_order: placement.build_order,
      })),
      projection: {
        candidateId: 'candidate-1',
        label: 'Candidate one',
        placements: [{
          facility_template_id: 'agri_support_a',
          local_body_id: 'body1',
          is_primary_port: false,
          build_order: 1,
        }],
      },
    });
  });
});
