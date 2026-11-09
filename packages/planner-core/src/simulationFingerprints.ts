import type {
  SimulateBuildPlacement,
  SimulateBuildRequest,
} from '@ed-finder/api-client/types';
import { resequence } from './placementHelpers';
import type { TopologyPlanSnapshot } from './topologySelection';

export function snapshotPlacementFingerprint(placement: SimulateBuildPlacement) {
  return {
    facility_template_id: placement.facility_template_id,
    local_body_id: placement.local_body_id ?? null,
    is_primary_port: Boolean(placement.is_primary_port),
    build_order: placement.build_order,
  };
}

export function previewInputFingerprint(
  systemId64: number,
  targetArchetype: string,
  placements: SimulateBuildPlacement[],
): string {
  return JSON.stringify({
    system_id64: systemId64,
    target_archetype: targetArchetype,
    placements: resequence(placements).map(snapshotPlacementFingerprint),
  });
}

export function simulationRequestFingerprint(
  request?: SimulateBuildRequest | null,
): string | null {
  if (!request) return null;
  return previewInputFingerprint(
    request.system_id64,
    request.target_archetype,
    request.placements,
  );
}

export function planSnapshotEmissionFingerprint(
  placements: SimulateBuildPlacement[],
  targetArchetype: string,
  projection: TopologyPlanSnapshot['projection'],
): string {
  return JSON.stringify({
    targetArchetype,
    placements: placements.map(snapshotPlacementFingerprint),
    projection: projection ? {
      candidateId: projection.candidateId,
      label: projection.label,
      placements: projection.placements.map(snapshotPlacementFingerprint),
    } : null,
  });
}
