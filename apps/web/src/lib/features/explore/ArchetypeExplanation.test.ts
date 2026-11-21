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
  type ArchetypeExplanationEntry,
  type ArchetypeExplanationIdentity,
  type ArchetypeExplanationResult,
  type ExploreSystem,
  type RankingProvenance,
} from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import { ARCHETYPE_KEYS, archetypeLabel } from './archetypes';
import ArchetypeExplanation from './ArchetypeExplanation.svelte';
// Conn's explanation as the live site answered it on 21 November 2026.
import conn from './fixtures/conn-explanation.json';

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

const ranking = conn.ranking as RankingProvenance;
const expected: ArchetypeExplanationIdentity = {
  derived_generation_id: ranking.derived_generation_id,
  archetype_version: ranking.archetype_version,
  archetype_manifest_sha256: ranking.archetype_manifest_sha256,
};
const system: ExploreSystem = {
  id64: parseId64('9466510583201'),
  name: 'Conn',
  overall_development_potential: 69,
  ranking_score: 69,
  ranking_tier: 'B',
  score_kind: 'overall_potential',
  selected_archetype: null,
  primary_archetype: 'stronghold',
  secondary_archetype: 'flexible',
  ranking_provenance: ranking,
};
const explained = (): Extract<
  ArchetypeExplanationResult,
  { kind: 'explained' }
> => ({
  kind: 'explained',
  id64: system.id64,
  ranking,
  completeness: conn.completeness,
  summary: conn.summary as Extract<
    ArchetypeExplanationResult,
    { kind: 'explained' }
  >['summary'],
  archetypes: conn.archetypes as unknown as ArchetypeExplanationEntry[],
});
const props = { system, expected, archetype: 'any' as const };
const mockedExplanation = vi.mocked(getArchetypeExplanation);

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  featureGate.enabled = true;
  mockedExplanation.mockResolvedValue(explained());
});
afterEach(() => {
  cleanup();
  queryClient.clear();
});

const region = (): HTMLElement =>
  screen.getByRole('region', { name: 'Why Conn got its score' });
const lead = (): HTMLElement =>
  region().querySelector<HTMLElement>('[data-explanation-lead]')!;

describe('why the selected result scored what it did', () => {
  it('asks once, as soon as it is shown, and says why in plain words', async () => {
    render(ArchetypeExplanation, props);
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    expect(mockedExplanation).toHaveBeenCalledExactlyOnceWith(
      system.id64,
      expected,
      expect.any(AbortSignal),
    );
    // For Any: the colony type the system suits best, whose score is its
    // Best Colony Potential.
    expect(lead()).toHaveAttribute('data-explanation-archetype', 'stronghold');
    expect(
      within(lead()).getByText('Stronghold needs Military and Industrial.'),
    ).toBeVisible();
    expect(
      within(lead())
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Military: 70 out of 100, from 2 bodies that suit it.',
      'Industrial: 75 out of 100, from 14 bodies that suit it.',
      '71 for how strong Military and Industrial are. The weakest counts most.',
      '× 1.00 for quality: how cleanly a colony here can specialise in them.',
      '× 0.90 for bodies: how many suit them, and how well. 8 or more for each gives the most.',
      '+ 5 because Military and Industrial are both 60 or more.',
    ]);
    expect(within(lead()).getByText('How that makes 69:')).toBeVisible();
    // No confidence wording, percentages or developer terms.
    expect(region()).not.toHaveTextContent(
      /confidence|%|archetype|opportunit|synergy/iu,
    );
  });

  it('keeps the other colony types folded until asked for, best first', async () => {
    render(ArchetypeExplanation, props);
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    const toggle = screen.getByRole('button', {
      name: 'Every other colony type',
    });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(
      region().querySelectorAll('[data-explanation-archetype]'),
    ).toHaveLength(1);
    await fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const others = document.getElementById(
      toggle.getAttribute('aria-controls')!,
    )!;
    const sections = [
      ...others.querySelectorAll<HTMLElement>('[data-explanation-archetype]'),
    ];
    expect(sections).toHaveLength(7);
    expect(
      sections.map((section) => section.dataset.explanationArchetype).sort(),
    ).toEqual(ARCHETYPE_KEYS.filter((key) => key !== 'stronghold').sort());
    const scores = sections.map((section) =>
      Number(section.querySelector('.explanation-score')!.textContent),
    );
    expect([...scores].sort((a, b) => b - a)).toEqual(scores);
    // Each has its own score, the API's tier and its reasons.
    const flexible = within(others).getByRole('region', {
      name: /^Flexible Multi-Role Colony/u,
    });
    expect(
      within(flexible).getByText('50', { selector: '.explanation-score' }),
    ).toBeVisible();
    expect(within(flexible).getByText('Tier C')).toHaveAttribute(
      'data-tier',
      'C',
    );
    expect(
      within(flexible).getByText(
        '2 of the 7 economies are strong enough here.',
      ),
    ).toBeVisible();
    // Opening and closing asks nothing more.
    await fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(others.querySelector('[data-explanation-archetype]')).toBeNull();
    expect(mockedExplanation).toHaveBeenCalledTimes(1);
  });

  it('leads with the chosen colony type when the list is ranked by one', async () => {
    render(ArchetypeExplanation, { ...props, archetype: 'research_hub' });
    const research = conn.archetypes.find(
      (entry) => entry.key === 'research_hub',
    )!;
    await screen.findByRole('heading', {
      name: `Why Research Hub scores ${research.score}`,
    });
    expect(lead()).toHaveAttribute(
      'data-explanation-archetype',
      'research_hub',
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Every other colony type' }),
    );
    expect(
      screen.getByRole('region', { name: /^Stronghold/u }),
    ).toHaveAttribute('data-explanation-archetype', 'stronghold');
    expect(archetypeLabel('research_hub')).toBe('Research Hub');
  });

  it('says it is loading until the answer arrives', async () => {
    let resolve!: (value: ArchetypeExplanationResult) => void;
    mockedExplanation.mockReturnValue(new Promise((done) => (resolve = done)));
    render(ArchetypeExplanation, props);
    expect(within(region()).getByRole('status')).toHaveTextContent(
      'Loading why it scored this…',
    );
    resolve(explained());
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    expect(within(region()).queryByRole('status')).toBeNull();
  });

  it('says the reasons cannot be shown when the scores have changed since', async () => {
    mockedExplanation.mockResolvedValue({ kind: 'explanation_unavailable' });
    render(ArchetypeExplanation, props);
    await waitFor(() =>
      expect(within(region()).getByRole('status')).toHaveTextContent(
        "The reasons for this score can't be shown: the scores have been updated since this list was made. Search again to see them.",
      ),
    );
    expect(region().querySelector('[data-explanation-archetype]')).toBeNull();
  });

  it.each([
    [404, 'This system has no colony type explanation yet.'],
    [503, 'Colony type explanations are temporarily unavailable.'],
  ])('bounds the %s state and can retry on demand', async (status, message) => {
    mockedExplanation.mockResolvedValueOnce({ kind: 'error', status });
    render(ArchetypeExplanation, props);
    expect(await within(region()).findByRole('alert')).toHaveTextContent(
      message,
    );
    await fireEvent.click(
      within(region()).getByRole('button', { name: 'Try again' }),
    );
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    expect(mockedExplanation).toHaveBeenCalledTimes(2);
  });

  it('asks again, and folds the others, when the system or the scores behind it change', async () => {
    const { rerender } = render(ArchetypeExplanation, props);
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    await fireEvent.click(
      screen.getByRole('button', { name: 'Every other colony type' }),
    );
    const changed = { ...expected, archetype_manifest_sha256: 'd'.repeat(64) };
    await rerender({ expected: changed });
    await waitFor(() => expect(mockedExplanation).toHaveBeenCalledTimes(2));
    expect(mockedExplanation).toHaveBeenLastCalledWith(
      system.id64,
      changed,
      expect.any(AbortSignal),
    );
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    expect(
      screen.getByRole('button', { name: 'Every other colony type' }),
    ).toHaveAttribute('aria-expanded', 'false');
    await rerender({ system: { ...system, id64: parseId64('42') } });
    await waitFor(() => expect(mockedExplanation).toHaveBeenCalledTimes(3));
    expect(mockedExplanation).toHaveBeenLastCalledWith(
      parseId64('42'),
      changed,
      expect.any(AbortSignal),
    );
  });

  it('ignores a late answer for a previous ranking identity', async () => {
    let resolvePrevious!: (value: ArchetypeExplanationResult) => void;
    mockedExplanation.mockReturnValueOnce(
      new Promise((resolve) => (resolvePrevious = resolve)),
    );
    const { rerender } = render(ArchetypeExplanation, props);
    const changed = { ...expected, archetype_manifest_sha256: 'e'.repeat(64) };
    await rerender({ expected: changed });
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
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
      screen.getByRole('heading', { name: 'Why Stronghold scores 69' }),
    ).toBeVisible();
    expect(region()).not.toHaveTextContent('99');
  });

  it('fails closed without the ranking identity and stays absent with the flag off', async () => {
    const { unmount } = render(ArchetypeExplanation, {
      ...props,
      expected: null,
    });
    expect(within(region()).getByRole('status')).toHaveTextContent(
      /^The reasons for this score can't be shown/u,
    );
    expect(mockedExplanation).not.toHaveBeenCalled();
    unmount();
    featureGate.enabled = false;
    const { container } = render(ArchetypeExplanation, props);
    expect(container.children).toHaveLength(0);
    expect(container.textContent).toBe('');
    expect(mockedExplanation).not.toHaveBeenCalled();
  });

  it('is accessible folded and unfolded', async () => {
    render(ArchetypeExplanation, props);
    await screen.findByRole('heading', { name: 'Why Stronghold scores 69' });
    for (const open of [false, true]) {
      if (open)
        await fireEvent.click(
          screen.getByRole('button', { name: 'Every other colony type' }),
        );
      const result = await axe.run(region(), {
        rules: { 'color-contrast': { enabled: false } },
      });
      expect(result.violations).toEqual([]);
    }
  });
});
