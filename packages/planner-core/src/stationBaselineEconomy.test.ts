import { describe, expect, it } from 'vitest';
import type { SystemBody } from '@ed-finder/api-client/types';
import { calculateStationBaselineEconomy, describeStationBaselineEconomy } from './stationBaselineEconomy';

describe('stationBaselineEconomy', () => {
  it('calculates real baseline percentages from the ED-Finder body economy profile formula', () => {
    const body = {
      id: 2,
      name: 'HMC Geo Body',
      body_type: 'Planet',
      subtype: 'High metal content world',
      geo_signal_count: 2,
    } as SystemBody;

    const baseline = calculateStationBaselineEconomy(body);

    expect(baseline.unavailableReason).toBeNull();
    expect(baseline.calculationSource).toContain('Mega Guide body economy profile');
    expect(baseline.segments).toEqual([
      { economy: 'Extraction', percent: 69, source: 'base' },
      { economy: 'Industrial', percent: 31, source: 'modifier' },
    ]);
  });

  it('keeps mixed body profiles explicit instead of collapsing them into one label', () => {
    const body = {
      id: 3,
      name: 'ELW Body',
      body_type: 'Planet',
      subtype: 'Earth-like world',
      is_earth_like: true,
    } as SystemBody;

    const baseline = calculateStationBaselineEconomy(body);

    expect(baseline.segments.map((segment) => segment.economy)).toEqual(['Tourism', 'HighTech', 'Agriculture', 'Military']);
    expect(describeStationBaselineEconomy(baseline)).toContain('Tourism');
    expect(describeStationBaselineEconomy(baseline)).toContain('Run Preview for validated outcome');
  });

  it('does not invent percentages when no documented body rule matches', () => {
    const body = {
      id: 4,
      name: 'Unknown Body',
      body_type: 'Planet',
      subtype: 'Unknown',
    } as SystemBody;

    const baseline = calculateStationBaselineEconomy(body);

    expect(baseline.segments).toEqual([]);
    expect(baseline.unavailableReason).toContain('No documented body-to-economy rule');
    expect(describeStationBaselineEconomy(baseline)).toContain('Inherited/contextual baseline unavailable');
  });

  it('does not treat ring-like subtype text as ring evidence', () => {
    const body = {
      id: 5,
      name: 'Subtype Only',
      body_type: 'Planet',
      subtype: 'Ringed-looking rocky body',
      is_ringed: null,
    } as SystemBody;

    const baseline = calculateStationBaselineEconomy(body);

    expect(baseline.segments.map((segment) => segment.economy)).toEqual(['Refinery']);
  });
});
