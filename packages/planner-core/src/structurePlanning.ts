import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';
import { templateLocationKind } from './structurePicker';
import { bodyDisplayName } from './buildPlanLayout';
import { normalisePlanningEconomy } from './planningEconomy';
import type { BodyPlannerLane } from './plannerTypes';

export interface PrerequisiteIssue {
  placementIndex: number;
  templateId: string;
  templateName: string;
  missing: string[];
}

export type PlacementLaneAssignment = BodyPlannerLane | 'unassigned';

export function laneDisabledReason(body: SystemBody, lane: BodyPlannerLane): string | null {
  if (lane !== 'surface') return null;
  if (body.is_water_world === true) return 'Surface limited: water world.';
  if (body.is_landable === false) return 'Surface limited: non-landable body.';
  return null;
}

export function templateMatchesLane(template: FacilityTemplate, lane: BodyPlannerLane): boolean {
  const location = templateLocationKind(template);
  if (lane === 'orbital') return location === 'orbital' || location === 'both';
  return location === 'surface' || location === 'both';
}

const ASTEROID_STATION_TOKENS = ['asteroid_station', 'asteroid station', 'asteroid_base', 'asteroid base'];

export function isAsteroidStationTemplate(template: FacilityTemplate): boolean {
  const haystack = [template.id, template.name, template.category]
    .filter((value): value is string => typeof value === 'string')
    .map((value) => value.toLowerCase())
    .join(' | ');
  return ASTEROID_STATION_TOKENS.some((token) => haystack.includes(token));
}

export function templatePhysicalIncompatibilityReason(
  template: FacilityTemplate,
  body: SystemBody,
  lane: BodyPlannerLane,
): string | null {
  const laneReason = laneDisabledReason(body, lane);
  if (laneReason) return laneReason;
  if (lane === 'orbital' && isAsteroidStationTemplate(template) && body.is_ringed !== true) {
    return 'Asteroid Station requires a ringed body.';
  }
  return null;
}

export function templateCanFitBody(template: FacilityTemplate, body: SystemBody, lane: BodyPlannerLane): boolean {
  if (templatePhysicalIncompatibilityReason(template, body, lane)) return false;
  if (!templateMatchesLane(template, lane)) return false;
  const location = templateLocationKind(template);
  if (location === 'surface' || lane === 'surface') {
    return body.is_landable === true && body.is_water_world !== true;
  }
  return true;
}

const SLOT_LANE_PREREQUISITE_TOKENS = [
  'orbital slot',
  'orbit slot',
  'orbit pad',
  'orbital pad',
  'surface slot',
  'ground slot',
  'surface pad',
  'landing pad',
  'ringed body',
  'landable body',
  'water world',
  'atmospheric body',
  'orbit lane',
  'surface lane',
  'lane available',
  'slot available',
];

export function isSlotOrLaneRequirementDescription(description: string): boolean {
  const value = description.toLowerCase();
  return SLOT_LANE_PREREQUISITE_TOKENS.some((token) => value.includes(token));
}

export function placementLaneForTemplate(
  template: FacilityTemplate | undefined,
  body: SystemBody | undefined,
  laneHint?: BodyPlannerLane | null,
): PlacementLaneAssignment {
  if (!template) return 'unassigned';
  const location = templateLocationKind(template);
  if (location === 'orbital') return 'orbital';
  if (location === 'surface') return 'surface';
  if (location === 'both') {
    if (laneHint && templateMatchesLane(template, laneHint)) {
      if (laneHint === 'surface' && body && laneDisabledReason(body, 'surface')) return 'unassigned';
      return laneHint;
    }
    return 'unassigned';
  }
  return 'unassigned';
}

export function templateDisplayName(template: FacilityTemplate): string {
  return template.name;
}

export function structureFamilyLabel(template: FacilityTemplate): string {
  const haystack = [
    template.id,
    template.name,
    template.category,
    template.allowed_location,
  ].join(' ').toLowerCase();

  if (haystack.includes('starport') || haystack.includes('coriolis') || haystack.includes('orbis') || haystack.includes('ocellus') || haystack.includes('dodecahedron')) {
    return 'Station / starport';
  }
  if (haystack.includes('outpost')) return 'Outpost';
  if (haystack.includes('installation')) return 'Installation';
  if (haystack.includes('settlement')) return 'Settlement';
  if (haystack.includes('hub')) return 'Hub';
  if (template.is_port) return templateLocationKind(template) === 'surface' ? 'Planetary port' : 'Orbital port';
  return readableLabel(template.category || 'Structure');
}

export function laneLabel(lane: BodyPlannerLane): string {
  return lane === 'orbital' ? 'Orbit' : 'Surface';
}

export function isContextualEconomyTemplate(template: FacilityTemplate | undefined): boolean {
  return Boolean(template?.is_port && !normalisePlanningEconomy(template.economy));
}

export function contextualEconomyLabel(template: FacilityTemplate | undefined): string | null {
  if (!isContextualEconomyTemplate(template)) return null;
  return 'Economy: Contextual - inherits from body/system plan. Run Preview for validated economy outcome.';
}

export function contextualRoleLabel(template: FacilityTemplate | undefined, placement?: SimulateBuildPlacement): string | null {
  if (!isContextualEconomyTemplate(template)) return null;
  if (placement?.is_primary_port) return 'Primary port';
  return template?.is_port ? 'Port infrastructure' : 'Contextual infrastructure';
}

export function templatePrerequisiteDescriptions(template: FacilityTemplate | undefined): string[] {
  const raw = template?.prerequisites;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => prerequisiteDescription(item))
    .filter((item): item is string => Boolean(item));
}

export function missingPrerequisitesForTemplate(
  template: FacilityTemplate | undefined,
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
): string[] {
  const prerequisites = templatePrerequisiteDescriptions(template)
    .filter((description) => !isSlotOrLaneRequirementDescription(description));
  if (prerequisites.length === 0) return [];
  const placedTemplates = placements
    .map((placement) => templates.find((candidate) => candidate.id === placement.facility_template_id))
    .filter((candidate): candidate is FacilityTemplate => Boolean(candidate));

  return prerequisites.filter((description) => !placedTemplates.some((placedTemplate) => prerequisiteMatchesTemplate(description, placedTemplate)));
}

export function missingPrerequisitesForPlacement(
  placement: SimulateBuildPlacement,
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
): string[] {
  const template = templates.find((candidate) => candidate.id === placement.facility_template_id);
  return missingPrerequisitesForTemplate(template, placements.filter((candidate) => candidate !== placement), templates);
}

export function buildPlanPrerequisiteIssues(
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
): PrerequisiteIssue[] {
  return placements
    .map((placement, placementIndex) => {
      const template = templates.find((candidate) => candidate.id === placement.facility_template_id);
      const missing = missingPrerequisitesForPlacement(placement, placements, templates);
      return {
        placementIndex,
        templateId: placement.facility_template_id,
        templateName: template ? templateDisplayName(template) : readableLabel(placement.facility_template_id),
        missing,
      };
    })
    .filter((issue) => issue.missing.length > 0);
}

export function prerequisiteSummaryLabel(issueCount: number): string {
  return `${issueCount} prerequisite warning${issueCount === 1 ? '' : 's'}`;
}

export function describePlacementTarget(body: SystemBody, lane: BodyPlannerLane): string {
  return `${bodyDisplayName(body)} / ${laneLabel(lane)}`;
}

function prerequisiteDescription(item: unknown): string | null {
  if (typeof item === 'string') return item.trim() || null;
  if (!item || typeof item !== 'object') return null;
  const record = item as Record<string, unknown>;
  const description = record.description;
  if (typeof description === 'string' && description.trim()) return description.trim();
  for (const key of ['facility', 'template', 'facility_template_id', 'prerequisite']) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return readableLabel(value);
  }
  const entries = Object.entries(record)
    .map(([key, value]) => `${readableLabel(key)} ${typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''}`.trim())
    .filter(Boolean);
  return entries.length > 0 ? entries.join(', ') : null;
}

function prerequisiteMatchesTemplate(description: string, template: FacilityTemplate): boolean {
  const descriptionTokens = significantTokens(description);
  if (descriptionTokens.length === 0) return false;
  const candidateLabels = [
    template.id,
    template.name,
    templateDisplayName(template),
    template.category,
    `${template.category} ${template.name}`,
    `${structureFamilyLabel(template)} ${template.name}`,
    sourceStructure(template),
  ].filter((item): item is string => Boolean(item));

  return candidateLabels.some((candidate) => {
    const candidateTokens = significantTokens(candidate);
    if (candidateTokens.length === 0) return false;
    const candidateInDescription = candidateTokens.every((token) => descriptionTokens.includes(token));
    const descriptionInCandidate = descriptionTokens.every((token) => candidateTokens.includes(token));
    return candidateInDescription || descriptionInCandidate;
  });
}

function sourceStructure(template: FacilityTemplate): string | null {
  const sourceFields = (template.stat_effects as { source_fields?: unknown } | undefined)?.source_fields;
  if (sourceFields && typeof sourceFields === 'object') {
    const structure = (sourceFields as Record<string, unknown>).structure;
    if (typeof structure === 'string') return structure;
  }
  return null;
}

function significantTokens(value: string): string[] {
  return Array.from(new Set(
    value
      .toLowerCase()
      .replace(/[_/|>➔→-]+/g, ' ')
      .replace(/[^a-z0-9 ]+/g, ' ')
      .split(/\s+/)
      .filter((token) => token.length > 2 && !['the', 'and', 'for', 'with'].includes(token)),
  ));
}

function readableLabel(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
