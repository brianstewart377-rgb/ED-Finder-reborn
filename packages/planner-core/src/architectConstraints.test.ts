import { describe, expect, it } from 'vitest';
import {
  emptyArchitectConstraints,
  normaliseArchitectConstraints,
} from './architectConstraints';

describe('architectConstraints', () => {
  it('returns stable defaults for empty input', () => {
    expect(normaliseArchitectConstraints(null)).toEqual(emptyArchitectConstraints());
    expect(normaliseArchitectConstraints(undefined)).toEqual(emptyArchitectConstraints());
  });

  it('normalises and deduplicates user-provided constraint lists', () => {
    const normalised = normaliseArchitectConstraints({
      mustProduce: ['CMM', ' CMM ', 'steel'],
      prefer: ['industrial', 'industrial'],
      avoid: ['tourism', ''],
      mainStationBody: ' A 5 ',
      primaryPortPolicy: 'outpost_only',
      scale: 'expansion',
      requiredStructures: ['orbital_port', 'orbital_port'],
      forbiddenStructures: ['colony_ship_only'],
      preserveExisting: false,
      maxWarnings: 4.8,
    });

    expect(normalised.mustProduce).toEqual(['CMM', 'steel']);
    expect(normalised.prefer).toEqual(['industrial']);
    expect(normalised.avoid).toEqual(['tourism']);
    expect(normalised.mainStationBody).toBe('A 5');
    expect(normalised.primaryPortPolicy).toBe('outpost_only');
    expect(normalised.scale).toBe('expansion');
    expect(normalised.requiredStructures).toEqual(['orbital_port']);
    expect(normalised.forbiddenStructures).toEqual(['colony_ship_only']);
    expect(normalised.preserveExisting).toBe(false);
    expect(normalised.maxWarnings).toBe(4);
  });

  it('falls back safely for invalid policy/scale/max warnings', () => {
    const normalised = normaliseArchitectConstraints({
      // @ts-expect-error intentional invalid values for runtime guard coverage
      scale: 'hyper',
      // @ts-expect-error intentional invalid values for runtime guard coverage
      primaryPortPolicy: 'anything',
      maxWarnings: -4,
    });

    expect(normalised.scale).toBeNull();
    expect(normalised.primaryPortPolicy).toBe('no_preference');
    expect(normalised.maxWarnings).toBe(0);
  });
});
