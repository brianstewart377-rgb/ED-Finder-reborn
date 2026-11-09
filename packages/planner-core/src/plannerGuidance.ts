import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';

export type PlannerGuidanceSeverity = 'info' | 'advisory' | 'caution' | 'high-risk' | 'incompatible';

export interface PlannerGuidanceItem {
  id: string;
  severity: PlannerGuidanceSeverity;
  text: string;
}

export interface PlannerGuidancePlacementInput {
  placement: SimulateBuildPlacement;
  template?: FacilityTemplate;
  body: SystemBody | null;
  hasUnknownBody?: boolean;
  warnings?: string[];
}

const severityByWarning: Array<[RegExp, PlannerGuidanceSeverity]> = [
  [/surface facility on water world/i, 'incompatible'],
  [/surface facility on non-landable body/i, 'incompatible'],
  [/template uses estimated data/i, 'advisory'],
  [/metadata is sparse|orbital suitability unclear|body-specific checks are unavailable/i, 'caution'],
  [/body id does not match known body|placement has no body|placement has no known body|facility template missing/i, 'caution'],
];

export function plannerSeverityForWarning(warning: string): PlannerGuidanceSeverity {
  return severityByWarning.find(([pattern]) => pattern.test(warning))?.[1] ?? 'advisory';
}

export function buildPlannerGuidanceForPlacement({
  placement,
  template,
  body,
  hasUnknownBody = false,
  warnings = [],
}: PlannerGuidancePlacementInput): PlannerGuidanceItem[] {
  const guidance = new Map<string, PlannerGuidanceItem>();
  const add = (item: PlannerGuidanceItem) => {
    const existing = guidance.get(item.text);
    if (!existing || severityRank(item.severity) > severityRank(existing.severity)) {
      guidance.set(item.text, item);
    }
  };

  for (const warning of warnings) {
    add({
      id: `warning:${warning}`,
      severity: plannerSeverityForWarning(warning),
      text: guidanceTextForWarning(warning),
    });
  }

  if (!template) {
    add({
      id: 'missing-template',
      severity: 'caution',
      text: 'Facility template is missing; confirm the plan before relying on this placement.',
    });
  }

  if (!body) {
    add({
      id: hasUnknownBody ? 'unknown-body' : 'unassigned-body',
      severity: 'caution',
      text: hasUnknownBody
        ? 'Unknown body assignment: confirm the body in game before relying on this placement.'
        : 'No body is assigned yet; body-specific checks are unavailable.',
    });
  }

  if (body && isSparseBody(body)) {
    add({
      id: 'sparse-body',
      severity: 'caution',
      text: 'Sparse body metadata: confirm in game before relying on this placement.',
    });
  }

  if (template?.confidence === 'estimated') {
    add({
      id: 'estimated-template',
      severity: 'advisory',
      text: 'Estimated template data: review before relying on the plan.',
    });
  }

  if (template && body?.is_water_world && isSurfaceTemplate(template)) {
    add({
      id: 'surface-water-world',
      severity: 'incompatible',
      text: 'Surface structure may be invalid on this body.',
    });
  }

  if (template && body?.is_landable === false && isSurfaceTemplate(template)) {
    add({
      id: 'surface-non-landable',
      severity: 'incompatible',
      text: 'Surface structure may be invalid on this body.',
    });
  }

  if (template && body?.is_water_world && isOrbitalTemplate(template)) {
    add({
      id: 'water-world-orbital',
      severity: 'advisory',
      text: 'Water-world orbital planning may favour tourism/agriculture.',
    });
  }

  if (shouldShowArchitectGuidance(placement, template)) {
    add({
      id: 'architect-check',
      severity: 'info',
      text: 'Architect primary-port location should be checked before final major station placement.',
    });
  }

  if (placement.is_primary_port) {
    add({
      id: 'primary-port-outpost-option',
      severity: 'advisory',
      text: 'If the flagged primary-port slot is inconvenient, consider an outpost there and place the main station elsewhere.',
    });
  }

  return Array.from(guidance.values()).sort((a, b) => (
    severityRank(b.severity) - severityRank(a.severity) || a.text.localeCompare(b.text)
  ));
}

export function buildPlannerGuidanceForBody(
  body: SystemBody | null,
  placements: PlannerGuidancePlacementInput[],
): PlannerGuidanceItem[] {
  const guidance = new Map<string, PlannerGuidanceItem>();
  const add = (item: PlannerGuidanceItem) => {
    const existing = guidance.get(item.text);
    if (!existing || severityRank(item.severity) > severityRank(existing.severity)) {
      guidance.set(item.text, item);
    }
  };

  for (const { warnings = [] } of placements) {
    for (const warning of warnings) {
      add({
        id: `body-warning:${warning}`,
        severity: plannerSeverityForWarning(warning),
        text: guidanceTextForWarning(warning),
      });
    }
  }

  if (!body) {
    add({
      id: 'body-unassigned',
      severity: 'caution',
      text: 'No known body context yet; confirm assignments before relying on Preview.',
    });
  } else if (isSparseBody(body)) {
    add({
      id: 'body-sparse',
      severity: 'caution',
      text: 'Sparse body metadata: confirm in game.',
    });
  }

  if (body?.is_water_world && placements.some(({ template }) => template && isOrbitalTemplate(template))) {
    add({
      id: 'body-water-world-orbital',
      severity: 'advisory',
      text: 'Water-world orbital planning may favour tourism/agriculture.',
    });
  }

  if (placements.some(({ placement, template }) => shouldShowArchitectGuidance(placement, template))) {
    add({
      id: 'body-architect-check',
      severity: 'info',
      text: 'Architect primary-port location should be checked before final major station placement.',
    });
  }

  if (placements.some(({ placement }) => placement.is_primary_port)) {
    add({
      id: 'body-primary-outpost-option',
      severity: 'advisory',
      text: 'If the flagged primary-port slot is inconvenient, consider an outpost there and place the main station elsewhere.',
    });
  }

  return Array.from(guidance.values()).sort((a, b) => (
    severityRank(b.severity) - severityRank(a.severity) || a.text.localeCompare(b.text)
  ));
}

export function guidanceTone(severity: PlannerGuidanceSeverity): 'info' | 'advisory' | 'caution' | 'risk' {
  if (severity === 'incompatible' || severity === 'high-risk') return 'risk';
  if (severity === 'caution') return 'caution';
  return severity;
}

function guidanceTextForWarning(warning: string): string {
  if (/surface facility on water world|surface facility on non-landable body/i.test(warning)) {
    return 'Surface structure may be invalid on this body.';
  }
  if (/template uses estimated data/i.test(warning)) {
    return 'Estimated template data: review before relying on the plan.';
  }
  if (/metadata is sparse|orbital suitability unclear/i.test(warning)) {
    return 'Sparse body metadata: confirm in game before relying on this placement.';
  }
  if (/body id does not match known body/i.test(warning)) {
    return 'Unknown body assignment: confirm the body in game before relying on this placement.';
  }
  if (/placement has no body|placement has no known body/i.test(warning)) {
    return 'No body is assigned yet; body-specific checks are unavailable.';
  }
  if (/facility template missing/i.test(warning)) {
    return 'Facility template is missing; confirm the plan before relying on this placement.';
  }
  return warning;
}

function shouldShowArchitectGuidance(placement: SimulateBuildPlacement, template?: FacilityTemplate): boolean {
  return Boolean(placement.is_primary_port || template?.is_port || (template?.tier ?? 0) >= 3);
}

function isSurfaceTemplate(template: FacilityTemplate): boolean {
  return template.allowed_location.toLowerCase().includes('surface');
}

function isOrbitalTemplate(template: FacilityTemplate): boolean {
  return template.allowed_location.toLowerCase().includes('orbital');
}

function isSparseBody(body: SystemBody): boolean {
  return !body.body_type && !body.subtype;
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
