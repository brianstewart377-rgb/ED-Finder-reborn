import type { FacilityTemplate } from '@ed-finder/api-client/types';
import {
  normalizeArchitectObservation,
  type ArchitectObservationInput,
} from './architectObservation';
import type { BodyGroup, GroupedPlacement } from './buildPlanLayout';
import type { PlannerGuidanceItem, PlannerGuidanceSeverity } from './plannerGuidance';

export function buildStrategicTopologyGuidanceForGroup(
  group: BodyGroup,
  allGroups: BodyGroup[] = [group],
  architectInput?: ArchitectObservationInput | null,
): PlannerGuidanceItem[] {
  const guidance = new Map<string, PlannerGuidanceItem>();
  const status = normalizeArchitectObservation(architectInput);
  const hasMainCandidate = groupHasMainStationCandidate(group);
  const hasPrimaryPlan = group.placements.some((item) => item.placement.is_primary_port);
  const hasSupport = group.placements.some((item) => isSupportPlacement(item));
  const hasOrbitalPlan = group.placements.some((item) => isOrbitalTemplate(item.template));
  const otherGroupsHaveMainCandidate = allGroups.some((item) => item.key !== group.key && groupHasMainStationCandidate(item));

  const add = (item: PlannerGuidanceItem) => addGuidance(guidance, item);

  if (!group.body) {
    add({
      id: 'strategic-body-unknown',
      severity: 'caution',
      text: 'Sparse metadata: confirm in game before treating this as a strategic body.',
    });
  } else if (isSparseBody(group.body)) {
    add({
      id: 'strategic-body-sparse',
      severity: 'caution',
      text: 'Sparse metadata: confirm in game.',
    });
  }

  if (hasMainCandidate) {
    add({
      id: 'strategic-main-station-candidate',
      severity: 'advisory',
      text: 'Main station candidate: current plan places a primary or major port here.',
    });
  }

  if (hasSupport && !hasMainCandidate && group.body) {
    add({
      id: 'strategic-support-body',
      severity: 'info',
      text: otherGroupsHaveMainCandidate
        ? 'Good support body: current plan keeps this body support-focused away from the main station candidate.'
        : 'Good support body: current placements are support-focused.',
    });
  }

  if (group.body && hasOrbitalPlan && hasTourismAgriculturePressure(group.body)) {
    add({
      id: 'strategic-tourism-agriculture-pressure',
      severity: 'advisory',
      text: 'Likely tourism/agriculture pressure: body context may favour reviewing tourism or agriculture support.',
    });
  }

  if (hasMainCandidate && status.primaryPortFlag.state !== 'observed') {
    add({
      id: 'strategic-primary-unknown',
      severity: 'info',
      text: 'Primary-port flag unknown: check Architect Mode before final station placement.',
    });
  }

  if (hasPrimaryPlan) {
    add({
      id: 'strategic-outpost-option',
      severity: 'advisory',
      text: 'Consider outpost on inconvenient primary-port slot and main station elsewhere.',
    });
  }

  return Array.from(guidance.values()).sort((a, b) => (
    severityRank(b.severity) - severityRank(a.severity) || a.text.localeCompare(b.text)
  ));
}

function groupHasMainStationCandidate(group: BodyGroup): boolean {
  return group.placements.some((item) => (
    item.placement.is_primary_port
    || Boolean(item.template?.is_port)
    || (item.template?.tier ?? 0) >= 3
  ));
}

function isSupportPlacement(item: GroupedPlacement): boolean {
  const template = item.template;
  if (!template) return false;
  return Boolean(template.is_support_facility || !template.is_port);
}

function isOrbitalTemplate(template: FacilityTemplate | undefined): boolean {
  return template?.allowed_location.toLowerCase().includes('orbital') ?? false;
}

function hasTourismAgriculturePressure(body: NonNullable<BodyGroup['body']>): boolean {
  return Boolean(body.is_water_world || body.is_earth_like || body.is_terraformable);
}

function isSparseBody(body: NonNullable<BodyGroup['body']>): boolean {
  return !body.body_type && !body.subtype;
}

function addGuidance(guidance: Map<string, PlannerGuidanceItem>, item: PlannerGuidanceItem) {
  const existing = guidance.get(item.text);
  if (!existing || severityRank(item.severity) > severityRank(existing.severity)) {
    guidance.set(item.text, item);
  }
}

function severityRank(severity: PlannerGuidanceSeverity): number {
  switch (severity) {
    case 'incompatible':
      return 4;
    case 'high-risk':
      return 3;
    case 'caution':
      return 2;
    case 'advisory':
      return 1;
    case 'info':
    default:
      return 0;
  }
}
