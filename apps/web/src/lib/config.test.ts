import { describe, expect, it } from 'vitest';
import {
  FEATURE_GATE_ENABLED_VALUE,
  FINDER_F4_ENABLED,
  FINDER_F4_ENV_NAME,
  featureGateEnabled,
  isFinderF4Enabled,
} from './config';

describe('build feature configuration', () => {
  it('names the exact Vite variable the Finder F4 gate reads', () => {
    expect(FINDER_F4_ENV_NAME).toBe('VITE_FINDER_F4_ENABLED');
    expect(FEATURE_GATE_ENABLED_VALUE).toBe('1');
  });

  it('is disabled by default: an absent variable hides Finder F4', () => {
    expect(isFinderF4Enabled({})).toBe(false);
    expect(isFinderF4Enabled({ VITE_FINDER_F4_ENABLED: undefined })).toBe(
      false,
    );
  });

  it('fails closed on every non-exact value', () => {
    for (const value of [
      '',
      '0',
      'true',
      'yes',
      'on',
      ' 1',
      '1 ',
      '01',
      true,
    ]) {
      expect(isFinderF4Enabled({ VITE_FINDER_F4_ENABLED: value })).toBe(false);
    }
  });

  it('enables only on the exact fixture opt-in', () => {
    expect(isFinderF4Enabled({ VITE_FINDER_F4_ENABLED: '1' })).toBe(true);
    expect(featureGateEnabled({ OTHER: '1' }, 'VITE_FINDER_F4_ENABLED')).toBe(
      false,
    );
  });

  it('keeps this test build default-off (no opt-in is configured for Vitest)', () => {
    expect(FINDER_F4_ENABLED).toBe(false);
  });
});
