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
  navigation_limit: 10_000,
  truncation,
});

describe('ranked card facts', () => {
  it('keeps overall potential, selected score, classification and evidence distinct and ordered', () => {
    render(FinderResultRanking, {
      variant: 'row',
      system,
      archetype: 'manufacturing_hub',
    });
    const overall = screen.getByText('Best Colony Potential: 91');
    const selected = screen.getByText(/Manufacturing Hub fit: 81/);
    const classification = screen.getByText(
      'Primary: Flexible Multi-Role Colony; Secondary: Megacomplex',
    );
    const label = screen.getByText('Close fit · Megacomplex 79');
    const evidence = screen.getByText('Fit confidence: moderate');
    expect(screen.getByText('Tier A')).toBeInTheDocument();
    expect(overall).not.toHaveTextContent('Tier');
    expect(selected.contains(label)).toBe(false);
    expect(evidence.contains(label)).toBe(false);
    expect(label.parentElement).toBe(classification.parentElement);
    expect(label).not.toHaveAttribute('aria-label');
    expect(evidence).toHaveAccessibleDescription(
      'Fit confidence 82%; evidence 64% complete',
    );
    const facts = [overall, selected, classification, evidence];
    for (let index = 1; index < facts.length; index += 1) {
      expect(
        facts[index - 1].compareDocumentPosition(facts[index]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
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
    expect(
      screen.getByText('Evidence confidence: moderate'),
    ).toHaveAccessibleDescription(
      'Evidence confidence 82%; evidence 64% complete',
    );
    expect(container.textContent?.toLowerCase()).not.toContain('fit');
    expect(container.innerHTML.toLowerCase()).not.toContain('clear fit');
    expect(container.innerHTML.toLowerCase()).not.toContain('close fit');
    expect(screen.queryByText('Tier A')).not.toBeInTheDocument();
  });

  it.each([
    [null, 0.8, 'unknown'],
    [0.49, 0.8, ': limited'],
    [0.5, 0.8, ': moderate'],
    [0.8, 0.8, ': strong'],
  ] as const)(
    'renders confidence %s and completeness %s as %s',
    (confidence, completeness, suffix) => {
      render(FinderResultRanking, {
        variant: 'row',
        system: { ...system, confidence, completeness },
        archetype: 'manufacturing_hub',
      });
      expect(
        screen.getByText(
          suffix === 'unknown'
            ? 'Fit confidence unknown'
            : `Fit confidence${suffix}`,
        ),
      ).toBeInTheDocument();
    },
  );

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

  it('keeps kit badges textual and evidence descriptions linked in both modes', async () => {
    const { container, rerender } = render(FinderResultRanking, {
      variant: 'row',
      system: { ...system, best_tier: 'S' },
      archetype: 'manufacturing_hub',
    });
    expect(screen.getByText('Tier S')).toHaveClass('kit-chip');
    expect(screen.getByText('Tier A')).toHaveClass('kit-chip');
    expect(screen.getByText('Close fit · Megacomplex 79')).toHaveClass(
      'kit-chip',
    );
    for (const archetype of ['manufacturing_hub', 'any'] as const) {
      await rerender({ archetype });
      expect(screen.getByText(/confidence: moderate/)).toHaveClass('kit-chip');
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
        page: page(10_000, { source: 'local-search', total_is_capped: false }),
      });
      expect(screen.getByText('10,000 matches')).toBeInTheDocument();
      expect(container.textContent).not.toContain('10,000+');
      await rerender({
        page: page(10_000, { source: 'local-search', total_is_capped: true }),
      });
      expect(
        screen.getByText(
          '10,000+ matches; navigation is limited to the first 10,000 results.',
        ),
      ).toBeInTheDocument();
      expect(container.textContent?.toLowerCase()).not.toContain('fit');
      expect(container.textContent).not.toContain('within the top');
    },
  );

  it('qualifies the selected ranking window only when the API marks it truncated', async () => {
    const { rerender } = render(FinderResultRanking, {
      variant: 'summary',
      archetype: 'manufacturing_hub',
      page: page(10_000, { source: 'rankings', is_truncated: true }),
    });
    expect(
      screen.getByText('within the top 10,000 for Manufacturing Hub'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Showing 3 of 10,000 (results 51–53)'),
    ).toBeInTheDocument();
    await rerender({
      page: page(10_000, { source: 'rankings', is_truncated: false }),
    });
    expect(screen.queryByText(/within the top/)).not.toBeInTheDocument();
    await rerender({
      page: page(153, { source: 'rankings', is_truncated: false }),
    });
    expect(screen.queryByText(/within the top/)).not.toBeInTheDocument();
    expect(
      screen.getByText('Showing 3 of 153 (results 51–53)'),
    ).toBeInTheDocument();
  });

  it.each([0, 9950])(
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
