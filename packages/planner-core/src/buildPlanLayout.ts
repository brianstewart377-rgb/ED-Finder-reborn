import type { FacilityTemplate, SimulateBuildPlacement, SimulateBuildResponse, SystemBody } from '@ed-finder/api-client/types';
import { bodyIdKey, sameBodyId } from './bodyId';
import { ARCHETYPES } from './simulationTypes';

export interface GroupedPlacement {
  placement: SimulateBuildPlacement;
  index: number;
  template?: FacilityTemplate;
  bodyId?: string;
  hasUnknownBody: boolean;
}

export interface BodyGroup {
  key: string;
  body: SystemBody | null;
  placements: GroupedPlacement[];
}

export interface PlanWarning {
  key: string;
  text: string;
}

export interface PlanSummary {
  systemName: string;
  targetArchetypeLabel: string;
  totalPlacements: number;
  assignedPlacements: number;
  unassignedPlacements: number;
  bodiesUsed: number;
  primaryPortStatus: 'none' | 'one' | 'multiple';
  primaryPortLabel: string;
  warningCount: number;
  yellowGenerated: number;
  greenGenerated: number;
  yellowNeeded: number;
  greenNeeded: number;
  previewStatus: 'not run' | 'stale' | 'running' | 'current';
  planWarnings: PlanWarning[];
}

export function groupPlacementsByBody(
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
  bodies: SystemBody[],
): BodyGroup[] {
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const bodiesById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );
  const bodyOrder = bodies
    .filter((body) => body.id != null)
    .map((body) => bodyIdKey(body.id));
  const groupsByKey = new Map<string, BodyGroup>();

  const ensureGroup = (key: string, body: SystemBody | null): BodyGroup => {
    const existing = groupsByKey.get(key);
    if (existing) return existing;
    const next = { key, body, placements: [] };
    groupsByKey.set(key, next);
    return next;
  };

  placements.forEach((placement, index) => {
    const bodyId = bodyIdKey(placement.local_body_id);
    const body = bodyId ? bodiesById.get(bodyId) ?? null : null;
    const key = body ? bodyId : 'unassigned';
    ensureGroup(key, body).placements.push({
      placement,
      index,
      template: templatesById.get(placement.facility_template_id),
      bodyId: bodyId || undefined,
      hasUnknownBody: Boolean(bodyId && !body),
    });
  });

  return Array.from(groupsByKey.values()).sort((a, b) => {
    if (a.key === 'unassigned') return 1;
    if (b.key === 'unassigned') return -1;
    const aIndex = bodyOrder.indexOf(a.key);
    const bIndex = bodyOrder.indexOf(b.key);
    if (aIndex !== -1 || bIndex !== -1) {
      return (aIndex === -1 ? Number.MAX_SAFE_INTEGER : aIndex)
        - (bIndex === -1 ? Number.MAX_SAFE_INTEGER : bIndex);
    }
    return a.key.localeCompare(b.key);
  });
}

export function getPlanSummary({
  systemName,
  targetArchetype,
  placements,
  templates,
  bodies,
  previewResult,
  isPreviewResultStale,
  runningPreview,
  groups = groupPlacementsByBody(placements, templates, bodies),
}: {
  systemName: string;
  targetArchetype: string;
  placements: SimulateBuildPlacement[];
  templates: FacilityTemplate[];
  bodies: SystemBody[];
  previewResult: SimulateBuildResponse | null;
  isPreviewResultStale: boolean;
  runningPreview: boolean;
  groups?: BodyGroup[];
}): PlanSummary {
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const assignedPlacements = placements.filter((placement) => {
    const bodyId = bodyIdKey(placement.local_body_id);
    return Boolean(bodyId && bodies.some((body) => sameBodyId(body.id, bodyId)));
  }).length;
  const bodiesUsed = groups.filter((group) => group.body && group.placements.length > 0).length;
  const primaryCount = placements.filter((placement) => placement.is_primary_port).length;
  const primaryPortStatus = primaryCount === 0 ? 'none' : primaryCount === 1 ? 'one' : 'multiple';
  const cp = placements.reduce((total, placement) => {
    const template = templatesById.get(placement.facility_template_id);
    if (!template) return total;
    total.yellowGenerated += template.yellow_cp_generated;
    total.greenGenerated += template.green_cp_generated;
    total.yellowNeeded += template.yellow_cp_cost;
    total.greenNeeded += template.green_cp_cost;
    return total;
  }, { yellowGenerated: 0, greenGenerated: 0, yellowNeeded: 0, greenNeeded: 0 });
  const planWarnings = getPlanWarnings({
    placements,
    groups,
    primaryPortStatus,
    yellowGenerated: cp.yellowGenerated,
    greenGenerated: cp.greenGenerated,
    yellowNeeded: cp.yellowNeeded,
    greenNeeded: cp.greenNeeded,
    previewResult,
    isPreviewResultStale,
  });
  const placementWarningCount = groups.reduce((count, group) => (
    count + getBodyGroupWarnings(group).length + group.placements.reduce((itemCount, item) => (
      itemCount + getPlacementWarnings(item, group.body).length
    ), 0)
  ), 0);

  return {
    systemName,
    targetArchetypeLabel: ARCHETYPES.find((item) => item.id === targetArchetype)?.label ?? targetArchetype,
    totalPlacements: placements.length,
    assignedPlacements,
    unassignedPlacements: placements.length - assignedPlacements,
    bodiesUsed,
    primaryPortStatus,
    primaryPortLabel: primaryPortStatus === 'none'
      ? 'No primary port'
      : primaryPortStatus === 'multiple'
        ? 'Multiple primary ports'
        : 'Primary port set',
    warningCount: planWarnings.length + placementWarningCount,
    yellowGenerated: cp.yellowGenerated,
    greenGenerated: cp.greenGenerated,
    yellowNeeded: cp.yellowNeeded,
    greenNeeded: cp.greenNeeded,
    previewStatus: runningPreview ? 'running' : !previewResult ? 'not run' : isPreviewResultStale ? 'stale' : 'current',
    planWarnings,
  };
}

function getPlanWarnings({
  placements,
  groups,
  primaryPortStatus,
  yellowGenerated,
  greenGenerated,
  yellowNeeded,
  greenNeeded,
  previewResult,
  isPreviewResultStale,
}: {
  placements: SimulateBuildPlacement[];
  groups: BodyGroup[];
  primaryPortStatus: PlanSummary['primaryPortStatus'];
  yellowGenerated: number;
  greenGenerated: number;
  yellowNeeded: number;
  greenNeeded: number;
  previewResult: SimulateBuildResponse | null;
  isPreviewResultStale: boolean;
}): PlanWarning[] {
  const warnings: PlanWarning[] = [];
  if (placements.length > 0 && primaryPortStatus === 'none') {
    warnings.push({ key: 'no-primary-port', text: 'No primary port selected' });
  }
  if (primaryPortStatus === 'multiple') {
    warnings.push({ key: 'multiple-primary-ports', text: 'Multiple primary ports selected' });
  }
  if (groups.some((group) => group.key === 'unassigned')) {
    warnings.push({ key: 'unassigned-placements', text: 'Some placements need body assignment' });
  }
  if (yellowNeeded > yellowGenerated || greenNeeded > greenGenerated) {
    warnings.push({ key: 'cp-pressure', text: 'CP needs may exceed visible CP generation' });
  }
  if (previewResult && isPreviewResultStale) {
    warnings.push({ key: 'preview-stale', text: 'Preview is stale' });
  }
  return warnings;
}

export function getBodyGroupSummary(group: BodyGroup) {
  return group.placements.reduce((summary, item) => {
    const template = item.template;
    if (!template) return summary;
    summary.hasPrimaryPort ||= Boolean(item.placement.is_primary_port);
    summary.yellowGenerated += template.yellow_cp_generated;
    summary.greenGenerated += template.green_cp_generated;
    summary.yellowNeeded += template.yellow_cp_cost;
    summary.greenNeeded += template.green_cp_cost;
    return summary;
  }, {
    hasPrimaryPort: group.placements.some((item) => item.placement.is_primary_port),
    yellowGenerated: 0,
    greenGenerated: 0,
    yellowNeeded: 0,
    greenNeeded: 0,
  });
}

export function getBodyGroupWarnings(group: BodyGroup): string[] {
  const warnings = new Set<string>();
  if (!group.body) {
    warnings.add('Needs review: placement has no known body');
  } else if (bodyTags(group.body).includes('Unknown body data')) {
    warnings.add('Data incomplete: body metadata is sparse');
  }
  for (const item of group.placements) {
    for (const warning of getBodySuitabilityWarnings(item.template, group.body)) {
      warnings.add(warning);
    }
  }
  return Array.from(warnings);
}

export function getPlacementWarnings(item: GroupedPlacement, body: SystemBody | null): string[] {
  const warnings = new Set<string>();
  if (!item.template) warnings.add('Needs review: facility template missing');
  if (item.template?.confidence === 'estimated') warnings.add('Needs review: template uses estimated data');
  if (!body && item.hasUnknownBody) warnings.add('Check placement: body ID does not match known body');
  if (!body && !item.hasUnknownBody) warnings.add('Needs review: placement has no body');
  for (const warning of getBodySuitabilityWarnings(item.template, body)) {
    warnings.add(warning);
  }
  return Array.from(warnings);
}

function getBodySuitabilityWarnings(template: FacilityTemplate | undefined, body: SystemBody | null): string[] {
  if (!template || !body) return [];
  const location = template.allowed_location.toLowerCase();
  const warnings: string[] = [];
  const isSurface = location.includes('surface');
  const isOrbital = location.includes('orbital');

  if (isSurface && body.is_water_world) {
    warnings.push('May be invalid: surface facility on water world');
  }
  if (isSurface && body.is_landable === false) {
    warnings.push('May be invalid: surface facility on non-landable body');
  }
  if (isOrbital && !body.body_type && !body.subtype) {
    warnings.push('Data incomplete: orbital suitability unclear');
  }
  return warnings;
}

export function getPlacementStatus(item: GroupedPlacement, body: SystemBody | null): string {
  if (!item.template) return 'missing template';
  if (!body) return item.hasUnknownBody ? 'unknown body' : 'unassigned';
  return 'planned';
}

export function bodyDisplayName(body: SystemBody): string {
  return body.name || (body.id != null ? `Body ${body.id}` : 'Unknown body');
}

export function compactBodyDisplayName(body: SystemBody, systemName?: string | null): string {
  const fullName = bodyDisplayName(body);
  const trimmedSystem = (systemName ?? '').trim();
  if (!trimmedSystem) return fullName;
  const prefix = `${trimmedSystem} `;
  if (!fullName.startsWith(prefix)) return fullName;
  const compact = fullName.slice(prefix.length).trim();
  return compact || fullName;
}

export function bodyTags(body: SystemBody): string[] {
  const tags = [
    body.body_type,
    body.subtype,
    body.is_landable ? 'Landable' : null,
    body.is_water_world ? 'Water world' : null,
    body.is_earth_like ? 'Earth-like' : null,
    body.is_ammonia_world ? 'Ammonia world' : null,
    body.is_terraformable ? 'Terraformable' : null,
    body.is_ringed === true ? 'Ringed' : body.is_ringed === false ? 'No rings' : null,
  ].filter((value): value is string => Boolean(value));

  const uniqueTags = Array.from(new Set(tags));
  return uniqueTags.length > 0 ? uniqueTags : ['Unknown body data'];
}
