import { describe, expect, it } from 'vitest';
import {
  ANY_ARCHETYPE,
  ANY_ARCHETYPE_DESCRIPTION,
  ANY_ARCHETYPE_LABEL,
  ARCHETYPES,
  ARCHETYPE_KEYS,
  CLEAR_FIT_MINIMUM_GAP,
  DEFAULT_MINIMUM_TIER,
  MINIMUM_TIER_SCORES,
  SELECTED_MINIMUM_TIERS,
  TIERS,
  archetypeLabel,
  archetypeMetadata,
  evidenceBadge,
  evidenceLevel,
  isArchetypeKey,
  isTier,
  minimumScoreForTier,
  parseArchetypeKey,
  parseMinimumTier,
  selectedFitLabel,
  tierLabel,
} from './archetypes';

describe('canonical V3 archetype metadata', () => {
  it('holds exactly the eight V3 keys in profile order', () => {
    expect(ARCHETYPE_KEYS).toEqual([
      'paradise',
      'mining_hub',
      'manufacturing_hub',
      'megacomplex',
      'research_hub',
      'stronghold',
      'population_capital',
      'flexible',
    ]);
    expect(ARCHETYPES.map((entry) => entry.key)).toEqual([...ARCHETYPE_KEYS]);
    expect(new Set(ARCHETYPES.map((entry) => entry.label)).size).toBe(8);
  });

  it('labels every key with the API display name and a one-line description', () => {
    expect(archetypeMetadata('flexible')).toEqual({
      key: 'flexible',
      label: 'Flexible Multi-Role Colony',
      description:
        'Several viable economies rather than one narrow specialism.',
    });
    expect(archetypeLabel('manufacturing_hub')).toBe('Manufacturing Hub');
    expect(archetypeLabel('paradise')).toBe('Paradise World');
    expect(archetypeLabel('population_capital')).toBe('Population Capital');
    for (const entry of ARCHETYPES) {
      expect(entry.description.length).toBeGreaterThan(20);
      expect(entry.description.endsWith('.')).toBe(true);
    }
  });

  it('describes Any separately from its label', () => {
    expect(ANY_ARCHETYPE).toBe('any');
    expect(ANY_ARCHETYPE_LABEL).toBe('Any');
    expect(ANY_ARCHETYPE_DESCRIPTION).toBe(
      'Show the best overall colony opportunities',
    );
  });

  it('fails closed on unknown, legacy and malformed keys', () => {
    for (const value of [
      'any',
      'Flexible',
      'FLEXIBLE',
      'mining',
      'agri_hub',
      '',
      null,
      undefined,
      1,
      {},
    ]) {
      expect(isArchetypeKey(value)).toBe(false);
      expect(parseArchetypeKey(value)).toBeNull();
    }
    expect(parseArchetypeKey('stronghold')).toBe('stronghold');
    expect(() => archetypeMetadata('legacy' as never)).toThrow(RangeError);
  });

  it('renders a historical key as a readable fallback, never as a V3 label', () => {
    expect(archetypeLabel('agri_hub')).toBe('agri hub');
    expect(archetypeLabel(null)).toBeNull();
    expect(archetypeLabel('')).toBeNull();
  });
});

describe('selected-archetype fit compared with its best rival', () => {
  it('has one named three-point clear-fit boundary', () => {
    expect(CLEAR_FIT_MINIMUM_GAP).toBe(3);
    expect(selectedFitLabel(82, 'megacomplex', 79)).toBe('Clear fit');
  });

  it.each([81, 79, 75])(
    'names the rival for a close, tied or losing selected score of %s',
    (score) => {
      expect(selectedFitLabel(score, 'megacomplex', 79)).toBe(
        'Close fit · Megacomplex 79',
      );
    },
  );

  it('does not invent a display name for an unknown rival key', () => {
    expect(selectedFitLabel(82, 'future_archetype', 79)).toBeNull();
    expect(selectedFitLabel(79, 'future_archetype', 79)).toBeNull();
  });

  it('omits the comparison when any input is absent or non-finite', () => {
    for (const absent of [undefined, null]) {
      expect(selectedFitLabel(absent, 'megacomplex', 79)).toBeNull();
      expect(selectedFitLabel(82, absent, 79)).toBeNull();
      expect(selectedFitLabel(82, 'megacomplex', absent)).toBeNull();
    }
    expect(selectedFitLabel(Number.NaN, 'megacomplex', 79)).toBeNull();
    expect(selectedFitLabel(82, 'megacomplex', Infinity)).toBeNull();
  });

  it('preserves finite zero as a measured score', () => {
    expect(selectedFitLabel(0, 'megacomplex', 0)).toBe(
      'Close fit · Megacomplex 0',
    );
  });
});

describe('tier presentation and selected-mode minimum tier', () => {
  it('always includes the word Tier in the visible label', () => {
    expect(TIERS).toEqual(['S', 'A', 'B', 'C', 'D']);
    for (const tier of TIERS) expect(tierLabel(tier)).toBe(`Tier ${tier}`);
    expect(isTier('S')).toBe(true);
    expect(isTier('s')).toBe(false);
    expect(isTier(null)).toBe(false);
  });

  it('maps S/A/B to the exact 88/76/60 floors with B as the default', () => {
    expect(SELECTED_MINIMUM_TIERS).toEqual(['S', 'A', 'B']);
    expect(MINIMUM_TIER_SCORES).toEqual({ S: 88, A: 76, B: 60 });
    expect(minimumScoreForTier('S')).toBe(88);
    expect(minimumScoreForTier('A')).toBe(76);
    expect(minimumScoreForTier('B')).toBe(60);
    expect(DEFAULT_MINIMUM_TIER).toBe('B');
    expect(minimumScoreForTier(DEFAULT_MINIMUM_TIER)).toBe(60);
  });

  it('rejects C and D (outside the initial product) and malformed values', () => {
    for (const value of ['C', 'D', 'b', ' B', 'B ', '', null, undefined, 60]) {
      expect(parseMinimumTier(value)).toBeNull();
    }
    expect(parseMinimumTier('A')).toBe('A');
  });
});

describe('honest uncertainty wording', () => {
  it('classifies at the 0.50 and 0.80 boundaries', () => {
    expect(evidenceLevel(null, 0.9)).toBe('unknown');
    expect(evidenceLevel(0.9, undefined)).toBe('unknown');
    expect(evidenceLevel(Number.NaN, 0.9)).toBe('unknown');
    expect(evidenceLevel(0.49, 0.9)).toBe('limited');
    expect(evidenceLevel(0.9, 0.49)).toBe('limited');
    expect(evidenceLevel(0.5, 0.5)).toBe('moderate');
    expect(evidenceLevel(0.79, 0.8)).toBe('moderate');
    expect(evidenceLevel(0.8, 0.79)).toBe('moderate');
    expect(evidenceLevel(0.8, 0.8)).toBe('strong');
    expect(evidenceLevel(1, 1)).toBe('strong');
  });

  it('uses Evidence confidence in Any and Fit confidence for a selected archetype', () => {
    expect(evidenceBadge('any', 0.82, 0.64)).toEqual({
      level: 'moderate',
      label: 'Evidence confidence: moderate',
      description: 'Evidence confidence 82%; evidence 64% complete',
    });
    expect(evidenceBadge('selected', 0.82, 0.64)).toEqual({
      level: 'moderate',
      label: 'Fit confidence: moderate',
      description: 'Fit confidence 82%; evidence 64% complete',
    });
    expect(evidenceBadge('selected', 0.4, 0.9).label).toBe(
      'Fit confidence: limited',
    );
    expect(evidenceBadge('any', 0.95, 0.85).label).toBe(
      'Evidence confidence: strong',
    );
  });

  it('says unknown when either value is missing and never synthesizes one', () => {
    expect(evidenceBadge('any', null, 0.7)).toEqual({
      level: 'unknown',
      label: 'Evidence confidence unknown',
      description: 'Evidence confidence unknown; evidence 70% complete',
    });
    expect(evidenceBadge('selected', 0.7, null)).toEqual({
      level: 'unknown',
      label: 'Fit confidence unknown',
      description: 'Fit confidence 70%; evidence completeness unknown',
    });
  });

  it('never uses the word fit in Any-mode visible or accessible text', () => {
    const samples: Array<[number | null, number | null]> = [
      [null, null],
      [0.1, 0.2],
      [0.5, 0.6],
      [0.9, 0.95],
    ];
    for (const [confidence, completeness] of samples) {
      const badge = evidenceBadge('any', confidence, completeness);
      expect(badge.label.toLowerCase()).not.toContain('fit');
      expect(badge.description.toLowerCase()).not.toContain('fit');
    }
  });
});
