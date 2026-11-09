import { describe, expect, it } from 'vitest';
import type { FacilityTemplate, SystemBody } from '@ed-finder/api-client/types';
import {
  getStructurePickerValidityLabel,
  getStructurePickerWarnings,
  locationMatchesFilter,
  resolveBodyContext,
  templateLocationKind,
} from './structurePicker';

const baseTemplate: FacilityTemplate = {
  id: 'template-a',
  name: 'Template A',
  category: 'support',
  tier: 1,
  economy: 'Industrial',
  is_port: false,
  is_support_facility: true,
  allowed_location: 'surface',
  pad_size: 'medium',
  confidence: 'observed',
  notes: null,
  yellow_cp_generated: 1,
  green_cp_generated: 0,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
};

describe('structurePickerUtils', () => {
  it('classifies location and filters templates', () => {
    const orbital = { ...baseTemplate, id: 'o', allowed_location: 'orbital' };
    const surface = { ...baseTemplate, id: 's', allowed_location: 'surface' };
    const both = { ...baseTemplate, id: 'b', allowed_location: 'surface_or_orbit' };

    expect(templateLocationKind(orbital)).toBe('orbital');
    expect(templateLocationKind(surface)).toBe('surface');
    expect(templateLocationKind(both)).toBe('both');
    expect(locationMatchesFilter(orbital, 'orbital')).toBe(true);
    expect(locationMatchesFilter(orbital, 'surface')).toBe(false);
    expect(locationMatchesFilter(both, 'both')).toBe(true);
    expect(locationMatchesFilter(surface, 'all')).toBe(true);
  });

  it('resolves body context for selected, none, and unknown body ids', () => {
    const bodies = [{ id: 42, name: 'A 42' }] as SystemBody[];
    expect(resolveBodyContext(bodies, null).status).toBe('none');
    expect(resolveBodyContext(bodies, '').status).toBe('none');
    const selected = resolveBodyContext(bodies, '42');
    expect(selected.status).toBe('selected');
    expect(selected.body?.name).toBe('A 42');
    const unknown = resolveBodyContext(bodies, '404');
    expect(unknown.status).toBe('unknown');
    expect(unknown.bodyId).toBe('404');
  });

  it('emits conservative body suitability warnings and validity labels', () => {
    const waterWorld = { id: 1, name: 'A 1', is_water_world: true, is_landable: true } as SystemBody;
    const nonLandable = { id: 2, name: 'A 2', is_landable: false } as SystemBody;
    const unknownMetadata = { id: 3 } as SystemBody;
    const surfaceTemplate = { ...baseTemplate, allowed_location: 'surface' };
    const orbitalTemplate = { ...baseTemplate, allowed_location: 'orbital' };
    const estimatedTemplate = { ...baseTemplate, confidence: 'estimated' };

    const noBodyWarnings = getStructurePickerWarnings(surfaceTemplate, { status: 'none', body: null, bodyId: null });
    expect(noBodyWarnings).toContain('Needs body: body-specific checks are unavailable');
    expect(getStructurePickerValidityLabel(surfaceTemplate, { status: 'none', body: null, bodyId: null })).toBe('Needs body');

    const unknownBodyWarnings = getStructurePickerWarnings(surfaceTemplate, { status: 'unknown', body: null, bodyId: '404' });
    expect(unknownBodyWarnings).toContain('Unknown body: body-specific checks are unavailable');
    expect(getStructurePickerValidityLabel(surfaceTemplate, { status: 'unknown', body: null, bodyId: '404' })).toBe('Unknown body');

    const waterWarnings = getStructurePickerWarnings(surfaceTemplate, { status: 'selected', body: waterWorld, bodyId: '1' });
    expect(waterWarnings).toContain('May be invalid: surface facility on water world');
    expect(getStructurePickerValidityLabel(surfaceTemplate, { status: 'selected', body: waterWorld, bodyId: '1' })).toBe('Check location');

    const landableWarnings = getStructurePickerWarnings(surfaceTemplate, { status: 'selected', body: nonLandable, bodyId: '2' });
    expect(landableWarnings).toContain('May be invalid: surface facility on non-landable body');

    const orbitalWarnings = getStructurePickerWarnings(orbitalTemplate, { status: 'selected', body: unknownMetadata, bodyId: '3' });
    expect(orbitalWarnings).toContain('Data incomplete: orbital suitability unclear');

    const estimatedWarnings = getStructurePickerWarnings(estimatedTemplate, { status: 'selected', body: waterWorld, bodyId: '1' });
    expect(estimatedWarnings).toContain('Needs review: template uses estimated data');
  });
});
