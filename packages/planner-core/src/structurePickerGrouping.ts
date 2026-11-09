import type { FacilityTemplate } from '@ed-finder/api-client/types';
import { templateLocationKind } from './structurePicker';

export interface StructurePickerGroup {
  label: string;
  templates: FacilityTemplate[];
}

const GROUP_ORDER = [
  'Orbital ports',
  'Surface settlements',
  'Ports',
  'Industrial support',
  'Agriculture support',
  'Extraction support',
  'Tourism support',
  'Military / security',
  'Support facilities',
  'Unknown / other',
] as const;

const GROUP_RANK = new Map<string, number>(GROUP_ORDER.map((label, index) => [label, index]));

export function deriveStructurePickerGroupLabel(template: FacilityTemplate): string {
  const category = normalize(template.category);
  const economy = normalize(template.economy);
  const location = templateLocationKind(template);

  if (template.is_port) {
    if (location === 'orbital') return 'Orbital ports';
    if (location === 'surface') return 'Surface settlements';
    return 'Ports';
  }

  if (matchesAny(economy, ['military', 'security']) || matchesAny(category, ['military', 'security'])) {
    return 'Military / security';
  }
  if (economy.includes('industrial') || category.includes('industrial')) {
    return 'Industrial support';
  }
  if (economy.includes('agriculture') || category.includes('agriculture')) {
    return 'Agriculture support';
  }
  if (
    economy.includes('extraction')
    || economy.includes('refinery')
    || category.includes('extraction')
    || category.includes('refinery')
  ) {
    return 'Extraction support';
  }
  if (economy.includes('tourism') || category.includes('tourism')) {
    return 'Tourism support';
  }
  if (template.is_support_facility) {
    return 'Support facilities';
  }

  return 'Unknown / other';
}

export function groupStructurePickerTemplates(templates: FacilityTemplate[]): StructurePickerGroup[] {
  const groups = new Map<string, FacilityTemplate[]>();
  for (const template of templates) {
    const label = deriveStructurePickerGroupLabel(template);
    const group = groups.get(label) ?? [];
    group.push(template);
    groups.set(label, group);
  }

  return Array.from(groups.entries())
    .map(([label, groupTemplates]) => ({
      label,
      templates: [...groupTemplates].sort(compareTemplates),
    }))
    .sort((a, b) => compareGroupLabels(a.label, b.label));
}

function compareGroupLabels(a: string, b: string): number {
  const aRank = GROUP_RANK.get(a) ?? GROUP_ORDER.length;
  const bRank = GROUP_RANK.get(b) ?? GROUP_ORDER.length;
  if (aRank !== bRank) return aRank - bRank;
  return a.localeCompare(b);
}

function compareTemplates(a: FacilityTemplate, b: FacilityTemplate): number {
  if (a.tier !== b.tier) return a.tier - b.tier;
  return a.name.localeCompare(b.name);
}

function normalize(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase();
}

function matchesAny(value: string, needles: string[]): boolean {
  return needles.some((needle) => value.includes(needle));
}
