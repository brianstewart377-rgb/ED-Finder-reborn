import { cleanup, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import type { ExploreSystem } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import FinderResultLine from './FinderResultLine.svelte';

afterEach(cleanup);

/** The text a screen reader is given: what is hidden from it left out. */
function spoken(element: Element): string {
  const copy = element.cloneNode(true) as Element;
  for (const hidden of copy.querySelectorAll('[aria-hidden="true"]'))
    hidden.remove();
  return (copy.textContent ?? '').replace(/\s+/g, ' ').trim();
}

// Teegarden's star as Find answered for it near Sol on 20 November 2026
// (cypress/fixtures/journeys/find-near-sol.json).
const teegarden: ExploreSystem = {
  id64: parseId64('5367098657608'),
  name: "Teegarden's star",
  overall_development_potential: 53,
  ranking_score: 53,
  ranking_tier: 'C',
  score_kind: 'overall_potential',
  selected_archetype: null,
  primary_archetype: 'stronghold',
  secondary_archetype: 'flexible',
  landable_count: 7,
  elw_count: 0,
  ww_count: 0,
  ammonia_count: 0,
  terraformable_count: 0,
  bio_signal_total: 1,
  geo_signal_total: 0,
};

function line(
  system: ExploreSystem,
  archetype: 'any' | 'manufacturing_hub' = 'any',
) {
  const { container } = render(FinderResultLine, { system, archetype });
  // The line's parts are laid out inside the result's button; here they sit
  // in the test container as they would in the button.
  return container;
}

describe('one result on one line', () => {
  it('gives the score the list is ranked by, with the API tier, and says what it is', () => {
    const container = line(teegarden);
    const score = container.querySelector('.line-score')!;
    expect(score).toHaveTextContent('Best Colony Potential: 53 Tier C');
    expect(score.querySelector('[data-tier="C"]')).toHaveTextContent('Tier C');
  });

  it('names both colony types, primary first, for reading and for screen readers', () => {
    const types = line(teegarden).querySelector('.line-types')!;
    expect(spoken(types)).toBe(
      'Primary: Stronghold; Secondary: Flexible Multi-Role Colony',
    );
    // On screen the two are joined by a dot that screen readers skip.
    expect(types.querySelector('[aria-hidden="true"]')).toHaveTextContent('·');
  });

  it('lists only the bodies and signals a system has, one or many', () => {
    const container = line({
      ...teegarden,
      landable_count: 1,
      ww_count: 2,
      bio_signal_total: 4,
    });
    expect(container.querySelector('.line-bodies')).toHaveTextContent(
      '1 landable · 2 water worlds · 4 bio signals',
    );
  });

  it('says when a system has none of them, and when its bodies are not known', () => {
    const none = line({
      ...teegarden,
      landable_count: 0,
      bio_signal_total: 0,
    });
    expect(none.querySelector('.line-bodies')).toHaveTextContent(
      'No notable bodies',
    );
    cleanup();
    const unknown = line({
      ...teegarden,
      landable_count: null,
      elw_count: null,
      ww_count: null,
      ammonia_count: null,
      terraformable_count: null,
      bio_signal_total: null,
      geo_signal_total: null,
    });
    expect(unknown.querySelector('.line-bodies')).toHaveTextContent(
      'Bodies not known',
    );
  });

  it('gives a colony type its fit and the fit tier, not the overall score', () => {
    const container = line(
      {
        ...teegarden,
        ranking_score: 79,
        ranking_tier: 'A',
        score_kind: 'selected_fit',
        selected_archetype: 'manufacturing_hub',
      },
      'manufacturing_hub',
    );
    const score = container.querySelector('.line-score')!;
    expect(score).toHaveAttribute('data-score-kind', 'fit');
    expect(score).toHaveTextContent('Manufacturing Hub fit: 79 Tier A');
    expect(score).not.toHaveTextContent('53');
  });

  it('never makes a tier up: no API tier, no tier shown, and an unknown score says so', () => {
    const container = line({
      ...teegarden,
      overall_development_potential: null,
      ranking_score: null,
      ranking_tier: null,
    });
    const score = container.querySelector('.line-score')!;
    expect(spoken(score)).toBe('Best Colony Potential: Unknown');
    expect(score.querySelector('[data-tier]')).toBeNull();
  });
});
