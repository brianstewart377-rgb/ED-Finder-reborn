import { cleanup, render, screen } from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it } from 'vitest';
import type { ExploreSystem, FinderPageEnvelope } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import FinderResultRanking from './FinderResultRanking.svelte';

afterEach(cleanup);

const system: ExploreSystem = {
  id64: parseId64('18446744073709551615'),
  overall_development_potential: 91,
  ranking_score: 81,
  ranking_tier: 'A',
  score_kind: 'selected_fit',
  selected_archetype: 'manufacturing_hub',
  primary_archetype: 'flexible',
  secondary_archetype: 'megacomplex',
  rival_archetype: 'megacomplex',
  rival_score: 79,
  confidence: 0.82,
  completeness: 0.64,
};

const page = (
  total: number,
  truncation: FinderPageEnvelope['truncation'],
): FinderPageEnvelope => ({
  page_size: 50,
  count: 3,
  total,
  navigable_total: total,
  offset: 50,
  navigation_limit: 1_000,
  truncation,
});

describe('ranked card facts', () => {
  it('keeps overall potential, selected score, classification and what is known distinct and ordered', () => {
    const { container } = render(FinderResultRanking, {
      variant: 'row',
      system,
      archetype: 'manufacturing_hub',
      bodiesKnown: '12 of 24 bodies known',
    });
    const overall = screen.getByText('Best Colony Potential: 91');
    const selected = screen.getByText(/Manufacturing Hub fit: 81/);
    const classification = screen.getByText(
      'Primary: Flexible Multi-Role Colony; Secondary: Megacomplex',
    );
    const label = screen.getByText('Close fit · Megacomplex 79');
    const evidence = screen.getByText('12 of 24 bodies known');
    expect(screen.getByText('Tier A')).toBeInTheDocument();
    expect(overall).not.toHaveTextContent('Tier');
    expect(selected.contains(label)).toBe(false);
    expect(evidence.contains(label)).toBe(false);
    expect(label.parentElement).toBe(classification.parentElement);
    expect(label).not.toHaveAttribute('aria-label');
    // How much is known is a plain fact: no confidence wording or percentages.
    expect(container).not.toHaveTextContent(/confidence|%/iu);
    const facts = [overall, selected, classification, evidence];
    for (let index = 1; index < facts.length; index += 1) {
      expect(
        facts[index - 1].compareDocumentPosition(facts[index]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it('says so when a claim ended and the outcome is unknown', () => {
    render(FinderResultRanking, {
      variant: 'row',
      system: {
        ...system,
        claim_status: 'claim_ended_unknown',
        claim_station_seen_at: '2026-08-12T23:30:00+00:00',
      },
      archetype: 'manufacturing_hub',
    });
    expect(screen.getByText('Claim ended, outcome unknown')).toBeVisible();
    expect(
      screen.getByText(
        'A station was last seen here on 12 Aug 2026. Someone claimed this system and either finished or gave up. Check in game before you travel.',
      ),
    ).toBeVisible();
  });

  it('still explains the note when the station date is missing or unreadable', () => {
    for (const seen of [null, 'not a date']) {
      const view = render(FinderResultRanking, {
        variant: 'row',
        system: {
          ...system,
          claim_status: 'claim_ended_unknown',
          claim_station_seen_at: seen,
        },
        archetype: 'manufacturing_hub',
      });
      expect(
        screen.getByText(
          'A station was seen here more than 30 days ago. Someone claimed this system and either finished or gave up. Check in game before you travel.',
        ),
      ).toBeVisible();
      view.unmount();
    }
  });

  it('shows no claim note for a free system', () => {
    for (const status of ['free', null, undefined] as const) {
      const view = render(FinderResultRanking, {
        variant: 'row',
        system: { ...system, claim_status: status },
        archetype: 'manufacturing_hub',
      });
      expect(screen.queryByText('Claim ended, outcome unknown')).toBeNull();
      expect(screen.queryByText(/Check in game/)).toBeNull();
      view.unmount();
    }
  });

  it('renders a supplied headline tier independently of the selected tier', () => {
    render(FinderResultRanking, {
      variant: 'row',
      system: { ...system, best_tier: 'S' },
      archetype: 'manufacturing_hub',
    });
    expect(screen.getByText('Tier S').parentElement).toHaveTextContent(
      'Best Colony Potential: 91',
    );
    expect(screen.getByText('Tier A').parentElement).toHaveTextContent(
      'Manufacturing Hub fit: 81',
    );
    for (const tier of ['Tier S', 'Tier A']) {
      expect(screen.getByText(tier)).toHaveAttribute(
        'data-tier',
        tier.slice(-1),
      );
    }
  });

  it('renders neither selected scores nor any fit wording in Any mode', () => {
    const { container } = render(FinderResultRanking, {
      variant: 'row',
      system: { ...system, secondary_archetype: null },
      archetype: 'any',
    });
    expect(screen.getByText('Best Colony Potential: 91')).toBeInTheDocument();
    expect(screen.getByText(/No clear secondary/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/confidence|%/iu);
    expect(container.textContent?.toLowerCase()).not.toContain('fit');
    expect(container.innerHTML.toLowerCase()).not.toContain('clear fit');
    expect(container.innerHTML.toLowerCase()).not.toContain('close fit');
    expect(screen.queryByText('Tier A')).not.toBeInTheDocument();
  });

  it('leaves the known-bodies line out when it is not given', () => {
    const { container } = render(FinderResultRanking, {
      variant: 'row',
      system,
      archetype: 'manufacturing_hub',
    });
    expect(container.querySelector('.bodies-known')).toBeNull();
  });

  it('says a system with no bodies on record is not scored', () => {
    // As the API answers for such a system: 0, and the first colony type.
    const { container } = render(FinderResultRanking, {
      variant: 'row',
      system: {
        ...system,
        body_count: 0,
        overall_development_potential: 0,
        ranking_score: 0,
        ranking_tier: 'D',
        score_kind: 'overall_potential',
        selected_archetype: null,
        primary_archetype: 'paradise',
        secondary_archetype: null,
      },
      archetype: 'any',
      bodiesKnown: 'No bodies known',
    });
    expect(screen.getByText('Not scored: no bodies known')).toBeVisible();
    expect(container).not.toHaveTextContent(
      /Best Colony Potential|Paradise World|Primary|Tier/u,
    );
    // Said once.
    expect(container.textContent?.match(/o bodies known/gu)).toHaveLength(1);
  });

  it('says no colony type fits a system that scores 0 with bodies on record', () => {
    const { container } = render(FinderResultRanking, {
      variant: 'row',
      system: {
        ...system,
        body_count: 2,
        overall_development_potential: 0,
        ranking_score: 0,
        ranking_tier: 'D',
        score_kind: 'overall_potential',
        selected_archetype: null,
        primary_archetype: 'paradise',
        secondary_archetype: null,
      },
      archetype: 'any',
      bodiesKnown: 'All 2 bodies known',
    });
    expect(screen.getByText('Best Colony Potential: 0')).toBeVisible();
    expect(
      screen.getByText('No colony type fits what is known here.'),
    ).toBeVisible();
    expect(container).not.toHaveTextContent(/Paradise World|Primary/u);
    expect(screen.getByText('All 2 bodies known')).toBeVisible();
  });

  it('preserves measured zero scores and keeps absent scores unknown', async () => {
    const { rerender } = render(FinderResultRanking, {
      variant: 'row',
      system: {
        ...system,
        overall_development_potential: 0,
        ranking_score: 0,
        ranking_tier: null,
        rival_score: 0,
      },
      archetype: 'manufacturing_hub',
    });
    expect(screen.getByText('Best Colony Potential: 0')).toBeInTheDocument();
    expect(screen.getByText('Manufacturing Hub fit: 0')).toBeInTheDocument();
    expect(screen.getByText('Close fit · Megacomplex 0')).toBeInTheDocument();
    // A best score of 0: no colony type is named as the one it suits.
    expect(
      screen.getByText('No colony type fits what is known here.'),
    ).toBeInTheDocument();
    await rerender({
      system: {
        ...system,
        overall_development_potential: null,
        ranking_score: null,
        ranking_tier: null,
        primary_archetype: null,
        secondary_archetype: null,
      },
    });
    expect(
      screen.getByText('Best Colony Potential: Unknown'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Manufacturing Hub fit: Unknown'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Primary: Unknown; Secondary: No clear secondary'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Close fit|Clear fit/)).not.toBeInTheDocument();
  });

  it('uses the selected integer score and rival, never classification confidence, for fit', () => {
    render(FinderResultRanking, {
      variant: 'row',
      system: { ...system, ranking_score: 82, archetype_confidence: 0 },
      archetype: 'manufacturing_hub',
    });
    expect(screen.getByText('Clear fit')).toBeInTheDocument();
  });

  it('keeps kit badges textual and accessible in both modes', async () => {
    const { container, rerender } = render(FinderResultRanking, {
      variant: 'row',
      system: { ...system, best_tier: 'S' },
      archetype: 'manufacturing_hub',
      bodiesKnown: 'All 8 bodies known',
    });
    expect(screen.getByText('Tier S')).toHaveClass('kit-chip');
    expect(screen.getByText('Tier A')).toHaveClass('kit-chip');
    expect(screen.getByText('Close fit · Megacomplex 79')).toHaveClass(
      'kit-chip',
    );
    for (const archetype of ['manufacturing_hub', 'any'] as const) {
      await rerender({ archetype });
      expect(screen.getByText('All 8 bodies known')).toBeVisible();
      expect(
        (
          await axe.run(container, {
            rules: { 'color-contrast': { enabled: false } },
          })
        ).violations,
      ).toEqual([]);
    }
  });
});

describe('ranking page count copy', () => {
  it.each(['anchored', 'galaxy-wide'])(
    '%s Any counts require an overflow sentinel',
    async () => {
      const { rerender, container } = render(FinderResultRanking, {
        variant: 'summary',
        archetype: 'any',
        page: page(153, { source: 'local-search', total_is_capped: false }),
      });
      expect(screen.getByText('153 matches')).toBeInTheDocument();
      expect(
        screen.getByText('Showing 3 of 153 (results 51–53)'),
      ).toBeInTheDocument();
      await rerender({
        page: page(1_000, { source: 'local-search', total_is_capped: false }),
      });
      expect(screen.getByText('1,000 matches')).toBeInTheDocument();
      expect(container.textContent).not.toContain('1,000+');
      await rerender({
        page: page(1_000, { source: 'local-search', total_is_capped: true }),
      });
      expect(
        screen.getByText(
          '1,000+ matches; navigation is limited to the first 1,000 results.',
        ),
      ).toBeInTheDocument();
      expect(container.textContent?.toLowerCase()).not.toContain('fit');
      expect(container.textContent).not.toContain('matches for');
    },
  );

  it('gives no total for a page whose matches were not counted, and says whether the count is on its way', async () => {
    const uncounted = page(53, {
      source: 'local-search',
      total_is_capped: false,
      total_is_counted: false,
    });
    const { rerender, container } = render(FinderResultRanking, {
      variant: 'summary',
      archetype: 'manufacturing_hub',
      page: uncounted,
      counting: true,
    });
    // 53 is how far the page reaches, not how many matches there are.
    expect(screen.getByText('Showing results 51–53')).toBeInTheDocument();
    expect(screen.getByText('Counting the matches…')).toBeInTheDocument();
    expect(container.textContent).not.toContain('of 53');
    expect(container.textContent).not.toContain('53 matches');
    // The count did not come.
    await rerender({ page: uncounted, counting: false });
    expect(
      screen.getByText(
        /^At least 53\s+matches; they could not be counted in\s+time\.$/u,
      ),
    ).toBeInTheDocument();
    expect(container.textContent).not.toContain('Counting');
    // The count came: the usual words.
    await rerender({
      page: page(153, { source: 'local-search', total_is_capped: false }),
      counting: false,
    });
    expect(
      screen.getByText('Showing 3 of 153 (results 51–53)'),
    ).toBeInTheDocument();
    expect(screen.getByText('153 matches')).toBeInTheDocument();
  });

  it('qualifies the selected ranking window only when the API marks it truncated', async () => {
    const { rerender } = render(FinderResultRanking, {
      variant: 'summary',
      archetype: 'manufacturing_hub',
      page: page(1_000, { source: 'rankings', is_truncated: true }),
    });
    expect(
      screen.getByText(
        '1,000+ matches for Manufacturing Hub; navigation is limited to the first 1,000 results.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Showing 3 of 1,000 (results 51–53)'),
    ).toBeInTheDocument();
    await rerender({
      page: page(1_000, { source: 'rankings', is_truncated: false }),
    });
    expect(screen.queryByText(/matches for/)).not.toBeInTheDocument();
    await rerender({
      page: page(153, { source: 'rankings', is_truncated: false }),
    });
    expect(screen.queryByText(/matches for/)).not.toBeInTheDocument();
    expect(
      screen.getByText('Showing 3 of 153 (results 51–53)'),
    ).toBeInTheDocument();
  });

  it.each([0, 950])(
    'counts a genuine empty page at offset %s from zero',
    (offset) => {
      render(FinderResultRanking, {
        variant: 'summary',
        archetype: 'any',
        page: {
          ...page(0, { source: 'local-search', total_is_capped: false }),
          count: 0,
          offset,
        },
      });
      expect(
        screen.getByText('Showing 0 of 0 (results 0–0)'),
      ).toBeInTheDocument();
    },
  );

  it('rejects a contradictory zero-total truncated ranking', () => {
    expect(() =>
      render(FinderResultRanking, {
        variant: 'summary',
        archetype: 'manufacturing_hub',
        page: {
          ...page(0, { source: 'rankings', is_truncated: true }),
          count: 0,
          offset: 0,
        },
      }),
    ).toThrow('A truncated ranking cannot have no qualifying rows');
  });
});
