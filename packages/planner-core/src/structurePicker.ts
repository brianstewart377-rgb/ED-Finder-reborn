import type { FacilityTemplate, SystemBody } from '@ed-finder/api-client/types';
import { bodyTags } from './buildPlanLayout';

export type StructurePickerLocationKind = 'orbital' | 'surface' | 'both' | 'unknown';
export type StructurePickerLocationFilter = 'all' | 'orbital' | 'surface' | 'both';

export interface StructurePickerBodyContext {
  status: 'selected' | 'none' | 'unknown';
  body: SystemBody | null;
  bodyId: string | null;
}

export function templateLocationKind(template: FacilityTemplate): StructurePickerLocationKind {
  const value = (template.allowed_location ?? '').toLowerCase();
  if (value.includes('surface') && value.includes('orbit')) return 'both';
  if (value.includes('surface')) return 'surface';
  if (value.includes('orbit')) return 'orbital';
  return 'unknown';
}

export function locationMatchesFilter(
  template: FacilityTemplate,
  filter: StructurePickerLocationFilter,
): boolean {
  if (filter === 'all') return true;
  return templateLocationKind(template) === filter;
}

export function resolveBodyContext(
  bodies: SystemBody[],
  selectedBodyId?: string | null,
): StructurePickerBodyContext {
  const bodyId = selectedBodyId != null && selectedBodyId !== '' ? String(selectedBodyId) : null;
  if (!bodyId) {
    return { status: 'none', body: null, bodyId: null };
  }
  const body = bodies.find((item) => item.id != null && String(item.id) === bodyId) ?? null;
  if (!body) {
    return { status: 'unknown', body: null, bodyId };
  }
  return { status: 'selected', body, bodyId };
}

export function getStructurePickerWarnings(
  template: FacilityTemplate,
  context: StructurePickerBodyContext,
): string[] {
  const warnings = new Set<string>();
  const location = templateLocationKind(template);
  if (template.confidence === 'estimated') {
    warnings.add('Needs review: template uses estimated data');
  }

  if (context.status === 'none') {
    warnings.add('Needs body: body-specific checks are unavailable');
    return Array.from(warnings);
  }

  if (context.status === 'unknown') {
    warnings.add('Unknown body: body-specific checks are unavailable');
    return Array.from(warnings);
  }

  const body = context.body;
  if (!body) {
    warnings.add('Unknown body: body-specific checks are unavailable');
    return Array.from(warnings);
  }

  if (bodyTags(body).includes('Unknown body data')) {
    warnings.add('Data incomplete: body metadata is sparse');
  }
  if (location === 'surface' && body.is_water_world) {
    warnings.add('May be invalid: surface facility on water world');
  }
  if (location === 'surface' && body.is_landable === false) {
    warnings.add('May be invalid: surface facility on non-landable body');
  }
  if (location === 'orbital' && !body.body_type && !body.subtype) {
    warnings.add('Data incomplete: orbital suitability unclear');
  }

  return Array.from(warnings);
}

export function getStructurePickerValidityLabel(
  template: FacilityTemplate,
  context: StructurePickerBodyContext,
): string {
  const warnings = getStructurePickerWarnings(template, context);
  if (context.status === 'none') return 'Needs body';
  if (context.status === 'unknown') return 'Unknown body';
  if (warnings.some((item) => item.startsWith('May be invalid'))) return 'Check location';
  if (warnings.length > 0) return 'Needs review';
  return 'Looks valid';
}
