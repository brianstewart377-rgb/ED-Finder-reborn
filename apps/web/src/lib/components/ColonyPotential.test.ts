import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getSystemArchetypes } from '$lib/api/client';
import type { SystemArchetypesWire } from '$lib/api/client';
import { queryClient } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import ColonyPotentialTestHost from './ColonyPotentialTestHost.svelte';
// LP 634-1 as the live site answered for it on 21 November 2026.
import lp6341 from './fixtures/lp-634-1-archetypes.json';

vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getSystemArchetypes: vi.fn(),
}));

const id64 = parseId64('6131166483162');
const answer = lp6341 as unknown as SystemArchetypesWire;

beforeEach(() => {
  queryClient.clear();
  vi.mocked(getSystemArchetypes).mockResolvedValue(answer);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Inspect's colony potential", () => {
  it('shows what Find showed: the score, its tier and what the system suits', async () => {
    render(ColonyPotentialTestHost, { props: { id64 } });
    const section = await screen.findByRole('region', {
      name: 'Colony potential',
    });
    await within(section).findByText('75', { selector: '.score' });
    expect(within(section).getAllByText('Tier B')[0]).toHaveAttribute(
      'data-tier',
      'B',
    );
    expect(section).toHaveTextContent(
      'Suits Flexible Multi-Role Colony, then Research Hub.',
    );
    expect(section).toHaveTextContent('Evidence confidence: moderate');
    expect(getSystemArchetypes).toHaveBeenCalledExactlyOnceWith(
      id64,
      expect.any(AbortSignal),
    );
  });

  it('lists every colony type, best first, each with its own tier', async () => {
    render(ColonyPotentialTestHost, { props: { id64 } });
    const list = await screen.findByRole('list', {
      name: 'Every colony type, best first',
    });
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(8);
    expect(rows[0]).toHaveTextContent(
      /^Flexible Multi-Role Colony\s*75\s*Tier B$/u,
    );
    expect(rows[1]).toHaveTextContent(/^Research Hub\s*44\s*Tier D$/u);
    const scores = rows.map((row) =>
      Number(row.querySelector('.type-score')!.textContent),
    );
    expect([...scores].sort((a, b) => b - a)).toEqual(scores);
  });

  it('waits until the Overview is shown before asking', async () => {
    const view = render(ColonyPotentialTestHost, {
      props: { id64, active: false },
    });
    expect(getSystemArchetypes).not.toHaveBeenCalled();
    await view.rerender({ id64, active: true });
    await screen.findByText('75', { selector: '.score' });
    expect(getSystemArchetypes).toHaveBeenCalledOnce();
  });

  it('says a system is not scored when Find has no scores for it', async () => {
    vi.mocked(getSystemArchetypes).mockRejectedValue(
      new ApiError(404, '/api/archetypes/system/6131166483162', {}),
    );
    render(ColonyPotentialTestHost, { props: { id64 } });
    expect(
      await screen.findByText(
        'Not scored. Find scores systems within 500 LY of inhabited space.',
      ),
    ).toBeInTheDocument();
    expect(getSystemArchetypes).toHaveBeenCalledOnce();
  });

  it('offers Retry when the scores could not be loaded', async () => {
    vi.mocked(getSystemArchetypes)
      .mockRejectedValueOnce(new ApiError(503, '/api/archetypes/system/x', {}))
      .mockRejectedValueOnce(new ApiError(503, '/api/archetypes/system/x', {}));
    render(ColonyPotentialTestHost, { props: { id64 } });
    // One retry is made first, after its usual short wait.
    const retry = await screen.findByRole(
      'button',
      { name: 'Retry' },
      { timeout: 4000 },
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Colony potential could not be loaded.',
    );
    await fireEvent.click(retry);
    await screen.findByText('75', { selector: '.score' });
  });
});
