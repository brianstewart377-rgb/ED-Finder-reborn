import { describe, expect, it } from 'vitest';
import {
  CORE_ECONOMY_ORDER,
  compactEconomyLabel,
  economyColor,
  normaliseCoreEconomy,
  normaliseEconomyName,
} from './economyVisuals';

describe('economyVisuals', () => {
  it('normalises supported economy names and aliases', () => {
    expect(normaliseCoreEconomy('High Tech')).toBe('HighTech');
    expect(normaliseCoreEconomy('high-tech')).toBe('HighTech');
    expect(normaliseCoreEconomy('Agricultural')).toBe('Agriculture');
    expect(normaliseEconomyName('Terraforming')).toBe('Terraforming');
  });

  it('maps every core economy to a stable readable colour', () => {
    CORE_ECONOMY_ORDER.forEach((economy) => {
      expect(economyColor(economy)).toMatch(/^#[0-9a-f]{6}$/i);
      expect(compactEconomyLabel(economy).length).toBeGreaterThan(0);
    });
    expect(economyColor('Refinery')).toBe('#fbbf24');
    expect(economyColor('Industrial')).toBe('#ff7a14');
    expect(economyColor('Extraction')).toBe('#94a3b8');
  });

  it('keeps contextual and unknown styling separate from real economies', () => {
    expect(economyColor('Contextual')).not.toBe(economyColor('Refinery'));
    expect(economyColor('Unknown')).not.toBe(economyColor('Extraction'));
    expect(economyColor('not a known economy')).toBe(economyColor('Unknown'));
  });
});
