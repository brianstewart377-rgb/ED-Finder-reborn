import { describe, expect, it } from 'vitest';
import type { FacilityTemplate } from '@ed-finder/api-client/types';
import { templateDisplayName, templatePrerequisiteDescriptions } from './structurePlanning';

const BASE_TEMPLATE: FacilityTemplate = {
  id: 'outpost',
  name: 'Outpost',
  category: 'port',
  tier: 1,
  economy: null,
  is_port: true,
  is_support_facility: false,
  allowed_location: 'orbital',
  pad_size: 'medium',
  confidence: 'confirmed',
  notes: null,
  yellow_cp_generated: 0,
  green_cp_generated: 0,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
};

describe('templateDisplayName', () => {
  it('returns the template name — the API never sends a separate display_name field', () => {
    expect(templateDisplayName(BASE_TEMPLATE)).toBe('Outpost');
  });
});

describe('templatePrerequisiteDescriptions', () => {
  it('reads prerequisites directly off the template without a cast', () => {
    const template: FacilityTemplate = {
      ...BASE_TEMPLATE,
      prerequisites: [{ description: 'Requires an Extraction economy' }, 'Requires tier 2'],
    };

    expect(templatePrerequisiteDescriptions(template)).toEqual([
      'Requires an Extraction economy',
      'Requires tier 2',
    ]);
  });

  it('returns an empty list when prerequisites is absent or not an array', () => {
    expect(templatePrerequisiteDescriptions(BASE_TEMPLATE)).toEqual([]);
    expect(templatePrerequisiteDescriptions(undefined)).toEqual([]);
  });
});
