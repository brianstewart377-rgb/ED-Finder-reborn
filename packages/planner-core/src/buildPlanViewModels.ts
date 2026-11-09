import type {
  FacilityTemplate,
  SimulateBuildPlacement,
  SimulateBuildResponse,
  SystemBody,
} from '@ed-finder/api-client/types';
import {
  bodyDisplayName,
  getBodyGroupSummary,
  getBodyGroupWarnings,
  getPlacementStatus,
  getPlacementWarnings,
  getPlanSummary,
  groupPlacementsByBody,
  type BodyGroup,
  type GroupedPlacement,
} from './buildPlanLayout';
import { buildColonyRoleHintsForGroup } from './colonyRoleHints';
import { buildLayoutTopologyReadout } from './layoutTopology';
import {
  buildPlannerGuidanceForBody,
  buildPlannerGuidanceForPlacement,
} from './plannerGuidance';
import { buildStrategicTopologyGuidanceForGroup } from './strategicTopologyGuidance';

export function buildPlanBodyViewModel(input: {
  systemName: string;
  targetArchetype: string;
  placements: SimulateBuildPlacement[];
  templates: FacilityTemplate[];
  bodies: SystemBody[];
  previewResult: SimulateBuildResponse | null;
  isPreviewResultStale: boolean;
  runningPreview: boolean;
}) {
  const groups = groupPlacementsByBody(input.placements, input.templates, input.bodies);
  return {
    groups,
    summary: getPlanSummary({ ...input, groups }),
  };
}

export function buildBodyGroupViewModel(group: BodyGroup, allGroups: BodyGroup[]) {
  const bodyWarnings = getBodyGroupWarnings(group);
  const bodyGuidance = buildPlannerGuidanceForBody(group.body, group.placements.map((item) => ({
    placement: item.placement,
    template: item.template,
    body: group.body,
    hasUnknownBody: item.hasUnknownBody,
    warnings: getPlacementWarnings(item, group.body),
  })));
  const isUnassigned = group.body === null;
  const title = isUnassigned || !group.body ? 'Unassigned / needs body' : bodyDisplayName(group.body);

  return {
    bodyWarnings,
    bodyGuidance,
    strategicGuidance: buildStrategicTopologyGuidanceForGroup(group, allGroups),
    roleHints: buildColonyRoleHintsForGroup(group, allGroups),
    summary: getBodyGroupSummary(group),
    topology: buildLayoutTopologyReadout(group),
    isUnassigned,
    body: group.body,
    title,
    placementLabel: `${group.placements.length} placement${group.placements.length === 1 ? '' : 's'}`,
    selectLabel: isUnassigned ? 'Select body Unassigned / needs body' : `Select body ${title}`,
  };
}

export function buildPlacementViewModel(item: GroupedPlacement, body: SystemBody | null) {
  const warnings = getPlacementWarnings(item, body);
  return {
    warnings,
    guidance: buildPlannerGuidanceForPlacement({
      placement: item.placement,
      template: item.template,
      body,
      hasUnknownBody: item.hasUnknownBody,
      warnings,
    }),
    status: getPlacementStatus(item, body),
    confidence: item.template?.confidence ?? 'missing',
    hasNotes: item.template?.notes != null && item.template.notes.trim().length > 0,
  };
}
