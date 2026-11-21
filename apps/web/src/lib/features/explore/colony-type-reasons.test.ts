import { describe, expect, it } from 'vitest';
import type { ArchetypeExplanationEntry } from '$lib/api/client';
import { colonyTypeReason } from './colony-type-reasons';
// The live site's own explanations, 21 November 2026, after the colony type
// re-score with the rule of v3-archetype-5.
import charickDrift from './fixtures/charick-drift-explanation.json';
import conn from './fixtures/conn-explanation.json';
import culann from './fixtures/culann-explanation.json';
// Sol as the frozen fixture set has it (tests/fixtures/ratings_v4_sources),
// scored by the real pipeline and served by the API's own explanation route,
// colony types by the rule of v3-archetype-5.
import sol from './fixtures/sol-explanation.json';

type Answer = { archetypes: ArchetypeExplanationEntry[] };
const entry = (answer: unknown, key: string): ArchetypeExplanationEntry =>
  (answer as Answer).archetypes.find((item) => item.key === key)!;

describe('why a colony type got its score, in plain words', () => {
  it('says why Conn is a 69 Stronghold', () => {
    expect(entry(conn, 'stronghold').score).toBe(69);
    expect(colonyTypeReason(entry(conn, 'stronghold'))).toEqual({
      needs: 'Stronghold needs Military and Industrial.',
      economies: [
        'Military: 70 out of 100, from 2 bodies that suit it.',
        'Industrial: 75 out of 100, from 14 bodies that suit it.',
      ],
      steps: [
        '71 for how strong Military and Industrial are. The weakest counts most.',
        '× 1.00 for quality: how cleanly a colony here can specialise in them.',
        '× 0.90 for bodies: how many suit them, and how well. 8 or more for each gives the most.',
        '+ 5 because Military and Industrial are both 60 or more.',
      ],
    });
  });

  it('shows what holds Culann at 59: fewer bodies', () => {
    expect(entry(culann, 'stronghold').score).toBe(59);
    const reason = colonyTypeReason(entry(culann, 'stronghold'));
    expect(reason.economies).toEqual([
      'Military: 74 out of 100, from 3 bodies that suit it.',
      'Industrial: 75 out of 100, from 4 bodies that suit it.',
    ]);
    expect(reason.steps[0]).toBe(
      '74.2 for how strong Military and Industrial are. The weakest counts most.',
    );
    expect(reason.steps[2]).toMatch(/^× 0\.73 for bodies/u);
  });

  it('names an economy no body suits, and leaves out a bonus not earned', () => {
    // Charick Drift: two stars, no planets.
    const reason = colonyTypeReason(entry(charickDrift, 'stronghold'));
    expect(reason.economies).toEqual([
      'Military: 62 out of 100, from 1 body that suits it.',
      'Industrial: 0 out of 100. No body here suits it.',
    ]);
    expect(reason.steps).toHaveLength(3);
    expect(reason.steps.join(' ')).not.toContain('because');
  });

  it('says what a type with one economy needs, and what adds its bonus', () => {
    const reason = colonyTypeReason(entry(charickDrift, 'research_hub'));
    expect(reason.needs).toBe(
      'Research Hub needs High Tech. Industrial adds a bonus when strong too.',
    );
    expect(reason.economies).toEqual([
      'High Tech: 62 out of 100, from 1 body that suits it.',
      'Industrial: 0 out of 100.',
    ]);
    expect(reason.steps[0]).toBe('62 for how strong High Tech is.');
    expect(reason.steps[1]).toMatch(/specialise in it\.$/u);
  });

  it('lists three economies with commas and "and"', () => {
    expect(colonyTypeReason(entry(conn, 'megacomplex')).needs).toBe(
      'Megacomplex needs Extraction, Refinery and Industrial.',
    );
  });

  it('says which three economies make Flexible, and how strong each is', () => {
    expect(entry(sol, 'flexible').score).toBe(70);
    expect(colonyTypeReason(entry(sol, 'flexible'))).toEqual({
      needs:
        'Flexible Multi-Role Colony needs three strong economies, of any kind. Its three strongest here:',
      economies: [
        'Industrial: 85 out of 100, from 26 bodies that suit it. With quality and bodies: 77.3.',
        'Extraction: 82 out of 100, from 13 bodies that suit it. With quality and bodies: 70.',
        'High Tech: 84 out of 100, from 6 bodies that suit it. With quality and bodies: 68.',
      ],
      steps: ['69.5 for the three together. The weakest counts most.'],
    });
  });

  it('says so when Flexible has fewer than three economies to draw on', () => {
    const flexible = entry(sol, 'flexible');
    const explanation = flexible.explanation as {
      components: Record<string, Record<string, number>>;
    };
    const thin = {
      ...flexible,
      explanation: {
        ...flexible.explanation,
        components: {
          ...explanation.components,
          pot: { ...explanation.components.pot, '4': 0 },
          opp_count: { ...explanation.components.opp_count, '4': 0 },
          strength: { ...explanation.components.strength, '4': 0 },
        },
      },
    } as ArchetypeExplanationEntry;
    expect(colonyTypeReason(thin).economies[2]).toBe(
      'High Tech: 0 out of 100. No body here suits it.',
    );
  });

  it('puts nothing into words that the explanation does not give', () => {
    const bare = {
      ...entry(conn, 'stronghold'),
      explanation: {},
    } as ArchetypeExplanationEntry;
    expect(colonyTypeReason(bare)).toEqual({
      needs: 'Stronghold: what it needs is not known.',
      economies: [],
      steps: [],
    });
  });
});
