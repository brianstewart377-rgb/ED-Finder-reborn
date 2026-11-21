import { describe, expect, it } from 'vitest';
import type { ArchetypeExplanationEntry } from '$lib/api/client';
import { colonyTypeReason } from './colony-type-reasons';
// The live site's own explanations, 21 November 2026.
import charickDrift from './fixtures/charick-drift-explanation.json';
import conn from './fixtures/conn-explanation.json';
import culann from './fixtures/culann-explanation.json';

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

  it('says how Flexible counts strong economies', () => {
    expect(entry(charickDrift, 'flexible').score).toBe(75);
    expect(colonyTypeReason(entry(charickDrift, 'flexible'))).toEqual({
      needs:
        'Flexible Multi-Role Colony needs several strong economies, of any kind.',
      economies: ['3 of the 7 economies are strong enough here.'],
      steps: ['Each one is worth 25, up to 4 of them.'],
    });
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
