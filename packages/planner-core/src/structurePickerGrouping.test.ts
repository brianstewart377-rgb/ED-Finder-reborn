import { describe, expect, it } from 'vitest';
import type { FacilityTemplate } from '@ed-finder/api-client/types';
import {
  deriveStructurePickerGroupLabel,
  groupStructurePickerTemplates,
} from './structurePickerGrouping';

const baseTemplate: FacilityTemplate = {
  id: 'base',
  name: 'Base',
  category: 'support',
  tier: 1,
  economy: null,
  is_port: false,
  is_support_facility: true,
  allowed_location: 'surface',
  pad_size: null,
  confidence: 'observed',
  notes: null,
  yellow_cp_generated: 0,
  green_cp_generated: 0,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
};

describe('structurePickerGroupingUtils', () => {
  it('derives conservative group labels from existing template fields', () => {
    expect(deriveStructurePickerGroupLabel({
      ...baseTemplate,
      id: 'orbital-port',
      is_port: true,
      is_support_facility: false,
      allowed_location: 'orbital',
    })).toBe('Orbital ports');

    expect(deriveStructurePickerGroupLabel({
      ...baseTemplate,
      id: 'surface-port',
      is_port: true,
      is_support_facility: false,
      allowed_location: 'surface',
    })).toBe('Surface settlements');

    expect(deriveStructurePickerGroupLabel({ ...baseTemplate, id: 'industrial', economy: 'Industrial' })).toBe('Industrial support');
    expect(deriveStructurePickerGroupLabel({ ...baseTemplate, id: 'agriculture', economy: 'Agriculture' })).toBe('Agriculture support');
    expect(deriveStructurePickerGroupLabel({ ...baseTemplate, id: 'extraction', economy: 'Extraction' })).toBe('Extraction support');
    expect(deriveStructurePickerGroupLabel({ ...baseTemplate, id: 'tourism', economy: 'Tourism' })).toBe('Tourism support');
    expect(deriveStructurePickerGroupLabel({ ...baseTemplate, id: 'military', category: 'security' })).toBe('Military / security');
    expect(deriveStructurePickerGroupLabel({
      ...baseTemplate,
      id: 'unknown',
      category: '',
      is_support_facility: false,
    })).toBe('Unknown / other');
  });

  it('groups templates under stable headings and sorts within each group', () => {
    const groups = groupStructurePickerTemplates([
      { ...baseTemplate, id: 'farm-2', name: 'Farm 2', tier: 2, economy: 'Agriculture' },
      { ...baseTemplate, id: 'port', name: 'Main Port', tier: 3, is_port: true, is_support_facility: false, allowed_location: 'orbital' },
      { ...baseTemplate, id: 'farm-1', name: 'Farm 1', tier: 1, economy: 'Agriculture' },
    ]);

    expect(groups.map((group) => group.label)).toEqual(['Orbital ports', 'Agriculture support']);
    expect(groups[1].templates.map((template) => template.id)).toEqual(['farm-1', 'farm-2']);
  });
});
