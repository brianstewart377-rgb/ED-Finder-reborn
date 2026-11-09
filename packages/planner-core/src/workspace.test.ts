import { describe, expect, it } from 'vitest';
import type { SystemDetail } from '@ed-finder/api-client/types';
import type { TopologyPlanSnapshot } from './topologySelection';
import { describeTopologySelection } from './topologySelection';
import {
  deriveArchitectStatus,
  getPlanHealthSummary,
  getPlanningFocusLabel,
  humanizeArchetype,
} from './workspace';

const system = {
  id64: 1,
  name: 'Utility System',
  bodies: [
    { id: 'body1', name: 'Utility System A 1', body_type: 'Planet', subtype: 'Rocky body' },
  ],
} as unknown as SystemDetail;

describe('workspaceUtils', () => {
  it('humanizes target archetype labels for user-facing planner copy', () => {
    expect(humanizeArchetype('refinery_industrial')).toBe('Refinery / Industrial Plan');
    expect(humanizeArchetype('tourism_agriculture')).toBe('Tourism / Agriculture Plan');
    expect(humanizeArchetype('military_security')).toBe('Military / Security Plan');
    expect(humanizeArchetype('trade_logistics')).toBe('Trade Logistics Plan');
  });

  it('derives architect status without claiming observation', () => {
    const empty: TopologyPlanSnapshot = { placements: [], templates: [], targetArchetype: 'refinery_industrial' };
    expect(deriveArchitectStatus(empty)).toBe('Architect flag not recorded');

    expect(deriveArchitectStatus({
      ...empty,
      placements: [{ facility_template_id: 'orbital_port', local_body_id: 'body1', is_primary_port: true, build_order: 1 }],
    })).toBe('Primary-port placement planned; Architect flag not recorded');
  });

  it('summarizes plan health and selected body focus', () => {
    const snapshot: TopologyPlanSnapshot = {
      placements: [
        { facility_template_id: 'orbital_port', local_body_id: 'body1', build_order: 1 },
        { facility_template_id: 'surface_hub', local_body_id: null, build_order: 2 },
        { facility_template_id: 'surface_hub', local_body_id: 'missing', build_order: 3 },
      ],
      templates: [],
      targetArchetype: 'refinery_industrial',
    };
    const selectedContext = describeTopologySelection({ type: 'body', bodyId: 'body1' }, system, snapshot);
    const health = getPlanHealthSummary({ snapshot, system, selectedContext, unsavedChanges: true });

    expect(health.placementCount).toBe(3);
    expect(health.unassignedCount).toBe(1);
    expect(health.warningCount).toBe(2);
    expect(health.saveStatus).toBe('Unsaved changes');
    expect(getPlanningFocusLabel({ type: 'body', bodyId: 'body1' }, system)).toBe('Utility System A 1');
  });
});
