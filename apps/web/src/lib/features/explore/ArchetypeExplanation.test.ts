import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getArchetypeExplanation,
  type ArchetypeExplanationIdentity,
  type ArchetypeExplanationResult,
  type ExploreSystem,
  type RankingProvenance,
} from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import { ARCHETYPE_KEYS, archetypeLabel } from './archetypes';
import ArchetypeExplanation from './ArchetypeExplanation.svelte';

const featureGate = vi.hoisted(() => ({ enabled: true }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getArchetypeExplanation: vi.fn(),
}));

const expected: ArchetypeExplanationIdentity = {
  derived_generation_id: '12345678-1234-1234-1234-123456789012',
  archetype_version: 'v3-archetype-4',
  archetype_manifest_sha256: 'a'.repeat(64),
};
const ranking: RankingProvenance = {
  ...expected,
  ranking_version: 'v1',
  ranking_sha256: 'b'.repeat(64),
  publication_sequence: 1,
  archetype_model_sha256: 'c'.repeat(64),
};
const system: ExploreSystem = {
  id64: parseId64('9223372036854775807'),
  name: 'Explanation fixture',
  ranking_score: 75,
  ranking_tier: 'B',
  score_kind: 'selected_fit',
  selected_archetype: 'flexible',
  confidence: 0.82,
  completeness: 0.64,
  ranking_provenance: ranking,
};
const explained = (): Extract<
  ArchetypeExplanationResult,
  { kind: 'explained' }
> => ({
  kind: 'explained',
  id64: system.id64,
  ranking,
  completeness: 0.64,
  summary: {
    primary_archetype: 'flexible',
    secondary_archetype: 'paradise',
    best_colony_potential: 75,
    best_tier: 'B',
    archetype_confidence: 0.3,
    weighted_potential: 48,
  },
  archetypes: ARCHETYPE_KEYS.map((key) => ({
    key,
    score: 75,
    tier: 'B',
    confidence: 0.82,
    weighted_e4: 393600,
    explanation:
      key === 'flexible'
        ? { anchors: [], breadth: 3, breadth_target: 4 }
        : {
            anchors: [1, 6],
            supporting: [3],
            core: 80.5,
            spec: 0.97,
            cap: 0.75,
            synergy: 5,
            components: {
              pot: { 1: 82, 6: 78, 3: 60 },
              qual: { 1: 70, 6: null },
              opp_count: { 1: 8, 6: 0 },
            },
          },
  })),
});
const props = {
  system,
  expected,
  archetype: 'flexible' as const,
  headingId: 'explanation-card-heading',
};
const mockedExplanation = vi.mocked(getArchetypeExplanation);
let heading: HTMLHeadingElement;

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  featureGate.enabled = true;
  mockedExplanation.mockResolvedValue(explained());
  heading = document.createElement('h2');
  heading.id = props.headingId;
  heading.textContent = system.name ?? '';
  document.body.append(heading);
});
afterEach(() => {
  cleanup();
  queryClient.clear();
  heading.remove();
});

async function expand(): Promise<HTMLElement> {
  await fireEvent.click(
    screen.getByRole('button', { name: 'Explain archetypes' }),
  );
  return screen.getByRole('region', { name: 'Explanation fixture' });
}

describe('archetype explanation disclosure', () => {
  it('fetches only on expansion and renders all eight stored scores, tiers and contributions', async () => {
    render(ArchetypeExplanation, props);
    const toggle = screen.getByRole('button', { name: 'Explain archetypes' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    expect(mockedExplanation).not.toHaveBeenCalled();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    const region = await expand();
    await waitFor(() =>
      expect(
        region.querySelectorAll('[data-explanation-archetype]'),
      ).toHaveLength(8),
    );
    expect(mockedExplanation).toHaveBeenCalledExactlyOnceWith(
      system.id64,
      expected,
      expect.any(AbortSignal),
    );
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', region.id);
    expect(region).toHaveAttribute('aria-labelledby', props.headingId);
    for (const key of ARCHETYPE_KEYS) {
      const section = within(region).getByRole('region', {
        name: archetypeLabel(key)!,
      });
      expect(section).toHaveAttribute('data-explanation-archetype', key);
      expect(
        within(section).getByText('75', { selector: '.explanation-score' })
          .parentElement,
      ).toHaveTextContent('Score: 75 · Tier B');
      expect(
        within(section).getByText('Fit confidence: moderate'),
      ).toHaveAccessibleDescription(
        'Fit confidence 82%; evidence 64% complete',
      );
    }
    const paradise = within(region).getByRole('region', {
      name: 'Paradise World',
    });
    expect(
      within(paradise).getByText('Required economies: Agriculture, Tourism.'),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText('Supporting economies: Industrial.'),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText(
        'Economy strength: 80.5 points before adjustments.',
      ),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText('Specialisation adjustment: ×0.97.'),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText('Opportunity capacity adjustment: ×0.75.'),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText('Economy synergy: 5 additional points.'),
    ).toBeInTheDocument();
    expect(
      within(paradise).getByText(
        /Tourism: potential 78 out of 100; quality unknown \(bounded estimate used\); 0 opportunities/,
      ),
    ).toBeInTheDocument();
    expect(
      within(region).getByText(
        '3 qualifying economies toward the target of 4.',
      ),
    ).toBeInTheDocument();

    await fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    await expand();
    expect(mockedExplanation).toHaveBeenCalledTimes(1);
  });

  it('shows a loading state and gives Any the same evidence wording as its card', async () => {
    let resolve!: (value: ArchetypeExplanationResult) => void;
    mockedExplanation.mockReturnValue(new Promise((done) => (resolve = done)));
    render(ArchetypeExplanation, { ...props, archetype: 'any' });
    const region = await expand();
    expect(within(region).getByRole('status')).toHaveTextContent(
      'Loading archetype explanations…',
    );
    resolve(explained());
    await waitFor(() =>
      expect(
        within(region).getAllByText('Evidence confidence: moderate'),
      ).toHaveLength(8),
    );
    expect(region.textContent?.toLowerCase()).not.toContain('fit');
    for (const badge of within(region).getAllByText(
      'Evidence confidence: moderate',
    )) {
      expect(badge).toHaveAccessibleDescription(
        'Evidence confidence 82%; evidence 64% complete',
      );
    }
  });

  it('renders the exact 409 copy without showing unmatched explanations', async () => {
    mockedExplanation.mockResolvedValue({ kind: 'explanation_unavailable' });
    render(ArchetypeExplanation, props);
    const region = await expand();
    expect(await within(region).findByRole('status')).toHaveTextContent(
      'Explanation unavailable for this ranking version',
    );
    expect(region.querySelector('[data-explanation-archetype]')).toBeNull();
  });

  it.each([
    [404, 'This system has no published archetype explanation.'],
    [503, 'Archetype explanations are temporarily unavailable.'],
  ])('bounds the %s state and can retry on demand', async (status, message) => {
    mockedExplanation.mockResolvedValueOnce({ kind: 'error', status });
    render(ArchetypeExplanation, props);
    const region = await expand();
    expect(await within(region).findByRole('alert')).toHaveTextContent(message);
    await fireEvent.click(
      within(region).getByRole('button', { name: 'Try again' }),
    );
    await waitFor(() =>
      expect(
        region.querySelectorAll('[data-explanation-archetype]'),
      ).toHaveLength(8),
    );
    expect(mockedExplanation).toHaveBeenCalledTimes(2);
  });

  it('requires a fresh expansion after a system or manifest change', async () => {
    const { rerender } = render(ArchetypeExplanation, props);
    await expand();
    await screen.findByText('3 qualifying economies toward the target of 4.');
    const changed = { ...expected, archetype_manifest_sha256: 'd'.repeat(64) };
    await rerender({ expected: changed });
    expect(
      screen.getByRole('button', { name: 'Explain archetypes' }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(mockedExplanation).toHaveBeenCalledTimes(1);
    await expand();
    await waitFor(() => expect(mockedExplanation).toHaveBeenCalledTimes(2));
    expect(mockedExplanation).toHaveBeenLastCalledWith(
      system.id64,
      changed,
      expect.any(AbortSignal),
    );
    await rerender({ system: { ...system, id64: parseId64('42') } });
    expect(
      screen.getByRole('button', { name: 'Explain archetypes' }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(mockedExplanation).toHaveBeenCalledTimes(2);
  });

  it('ignores a late response from a previous ranking identity', async () => {
    let resolvePrevious!: (value: ArchetypeExplanationResult) => void;
    mockedExplanation.mockReturnValueOnce(
      new Promise((resolve) => (resolvePrevious = resolve)),
    );
    const { rerender } = render(ArchetypeExplanation, props);
    await expand();
    const changed = { ...expected, archetype_manifest_sha256: 'e'.repeat(64) };
    await rerender({ expected: changed });
    const region = await expand();
    await within(region).findByText(
      '3 qualifying economies toward the target of 4.',
    );
    const previous = explained();
    resolvePrevious({
      ...previous,
      archetypes: previous.archetypes.map((entry) => ({ ...entry, score: 99 })),
    });
    await waitFor(() =>
      expect(
        queryClient.getQueryData(
          queryKeys.archetypeExplanation(system.id64, expected),
        ),
      ).toMatchObject({
        archetypes: expect.arrayContaining([
          expect.objectContaining({ score: 99 }),
        ]),
      }),
    );
    expect(
      within(region).getAllByText('75', { selector: '.explanation-score' }),
    ).toHaveLength(8);
    expect(within(region).queryByText('99')).not.toBeInTheDocument();
  });

  it('fails closed without the ranking identity and stays absent with the flag off', async () => {
    const { unmount } = render(ArchetypeExplanation, {
      ...props,
      expected: null,
    });
    const region = await expand();
    expect(within(region).getByRole('status')).toHaveTextContent(
      'Explanation unavailable for this ranking version',
    );
    expect(mockedExplanation).not.toHaveBeenCalled();
    unmount();
    featureGate.enabled = false;
    const { container } = render(ArchetypeExplanation, props);
    expect(container.children).toHaveLength(0);
    expect(container.textContent).toBe('');
    expect(mockedExplanation).not.toHaveBeenCalled();
  });

  it('has an accessible expanded disclosure and contribution lists', async () => {
    render(ArchetypeExplanation, props);
    const region = await expand();
    await screen.findByText('3 qualifying economies toward the target of 4.');
    const result = await axe.run(region, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
