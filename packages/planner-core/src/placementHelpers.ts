import type { FacilityTemplate, SimulateBuildPlacement, SystemBody } from '@ed-finder/api-client/types';
import type { RecommendedStep } from './simulationTypes';

export function resequence(items: SimulateBuildPlacement[]): SimulateBuildPlacement[] {
  return items.map((item, index) => ({ ...item, build_order: index + 1 }));
}

export function buildRecommendedPlacements(
  steps: RecommendedStep[],
  templates: FacilityTemplate[],
  bodies: SystemBody[],
): SimulateBuildPlacement[] {
  if (steps.length === 0 || templates.length === 0) return [];
  const byId = new Map(templates.map((template) => [template.id, template]));
  let primaryPortAssigned = false;
  const placements: SimulateBuildPlacement[] = [];

  for (const step of steps) {
    const facilityId = step.facility_id;
    if (!facilityId) continue;
    const template = byId.get(facilityId);
    if (!template) continue;
    const isPrimaryPort = template.is_port && !primaryPortAssigned;
    if (isPrimaryPort) {
      primaryPortAssigned = true;
    }
    placements.push({
      facility_template_id: template.id,
      local_body_id: recommendedBodyId(step.location, template, bodies),
      is_primary_port: isPrimaryPort,
      build_order: placements.length + 1,
    });
  }

  return resequence(placements);
}

export function recommendedBodyId(
  location: string | null | undefined,
  template: FacilityTemplate,
  bodies: SystemBody[],
): string | null {
  if (bodies.length === 0) return null;
  const locationText = `${location ?? ''} ${template.allowed_location}`.toLowerCase();
  const body = locationText.includes('surface')
    ? bodies.find((item) => item.is_landable) ?? bodies[0]
    : bodies[0];
  return body?.id != null ? String(body.id) : null;
}

export function preferredTemplate(templates: FacilityTemplate[]): FacilityTemplate | undefined {
  return templates.find((item) => item.is_port) ?? templates[0];
}

export function simulationBodies(bodies?: SystemBody[]): SystemBody[] {
  return (bodies ?? []).filter((body) => body.body_type !== 'Star');
}

export function archetypeFromEconomy(economy?: string | null): string | null {
  const normalised = (economy ?? '').toLowerCase();
  if (normalised.includes('refinery')) return 'refinery_industrial';
  if (normalised.includes('extraction')) return 'extraction_refinery';
  if (normalised.includes('agriculture')) return 'agriculture_terraforming';
  if (normalised.includes('hightech') || normalised.includes('high tech') || normalised.includes('tourism')) return 'hitech_tourism';
  if (normalised.includes('military')) return 'military_industrial';
  if (normalised.includes('industrial')) return 'refinery_industrial';
  return null;
}
