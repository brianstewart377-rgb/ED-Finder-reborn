import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '$lib/api/client';
import { auth, type AuthState } from '$lib/auth/auth';
import JournalAccountPanelTestHost from './JournalAccountPanelTestHost.svelte';

vi.mock('$lib/api/client', () => ({
  getVerifiedCommanders: vi.fn(),
  importVerifiedJournals: vi.fn(),
  offerGalaxyFacts: vi.fn(),
  getGalaxyContributions: vi.fn(),
  withdrawGalaxyContribution: vi.fn(),
  getGalaxyImpact: vi.fn(),
}));
vi.mock('$lib/journal/parse', () => ({ streamJournals: vi.fn() }));
vi.mock('$lib/journal/uploader', () => ({ uploadJournalBatches: vi.fn() }));
vi.mock('$lib/auth/auth', async () => {
  const { writable } = await import('svelte/store');
  return { auth: { ...writable({}), signIn: vi.fn() } };
});

const authState = auth as typeof auth & { set: (value: AuthState) => void };
const signedIn: AuthState = {
  authenticated: true,
  loading: false,
  user: {
    account_id: 'account-one',
    commander_name: 'Explorer',
    is_owner: false,
  },
  ownerClaimAvailable: false,
  error: null,
};
const populated = {
  systems_discovered: 1302,
  bodies_scanned: 9000,
  earth_like_worlds: 6,
  water_worlds: 4,
  ammonia_worlds: 2,
  terraformable_candidates: 12,
  gas_giants: 301,
};
const empty = {
  systems_discovered: 0,
  bodies_scanned: 0,
  earth_like_worlds: 0,
  water_worlds: 0,
  ammonia_worlds: 0,
  terraformable_candidates: 0,
  gas_giants: 0,
};

describe('Your Galaxy Impact', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    authState.set(signedIn);
    vi.mocked(api.getVerifiedCommanders).mockResolvedValue([]);
    vi.mocked(api.getGalaxyContributions).mockResolvedValue([]);
    vi.mocked(api.getGalaxyImpact).mockResolvedValue(populated);
  });
  afterEach(cleanup);

  it('shows cumulative personal totals and notable finds with accessible labels', async () => {
    render(JournalAccountPanelTestHost);
    const panel = await screen.findByRole('region', {
      name: 'Your Galaxy Impact',
    });
    await within(panel).findByText('9,000');
    const expected = [
      ['Systems discovered', '1,302'],
      ['Bodies scanned', '9,000'],
      ['Earth-Like Worlds', '6'],
      ['Water Worlds', '4'],
      ['Ammonia Worlds', '2'],
      ['Terraformable candidates', '12'],
      ['Gas giants', '301'],
    ];
    for (const [label, count] of expected) {
      const term = within(panel).getByText(label);
      expect(term.tagName).toBe('DT');
      expect(term.nextElementSibling?.tagName).toBe('DD');
      expect(term.nextElementSibling).toHaveTextContent(count);
    }
    expect(panel).toHaveTextContent(/recorded/i);
    expect(panel).toHaveTextContent(/first-discovery credit/i);
    expect(panel).toHaveTextContent(/stars and planets/i);
    expect(panel).not.toHaveTextContent(/facts|Planet ID|id64|schema/i);
    expect(api.getGalaxyImpact).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it('keeps all zero totals visible and invites private journal imports', async () => {
    vi.mocked(api.getGalaxyImpact).mockResolvedValue(empty);
    render(JournalAccountPanelTestHost);
    const panel = await screen.findByRole('region', {
      name: 'Your Galaxy Impact',
    });
    await within(panel).findByText(/Import your journals to start/i);
    expect(within(panel).getAllByText('0')).toHaveLength(7);
    expect(panel).toHaveTextContent(/Sharing is optional/i);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });

  it('separates loading and retryable failure from an empty commander', async () => {
    let reject!: (error: Error) => void;
    vi.mocked(api.getGalaxyImpact).mockImplementationOnce(
      () =>
        new Promise((_resolve, rejectPromise) => {
          reject = rejectPromise;
        }),
    );
    render(JournalAccountPanelTestHost);
    const panel = await screen.findByRole('region', {
      name: 'Your Galaxy Impact',
    });
    expect(within(panel).getByRole('status')).toHaveTextContent(
      'Loading your impact',
    );
    expect(within(panel).queryByText('0')).not.toBeInTheDocument();
    reject(new Error('v3_private.journal_event facts system_id64=123456'));
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('Your impact could not be loaded');
    expect(panel).not.toHaveTextContent(/v3_private|facts|123456/);
    await fireEvent.click(
      within(panel).getByRole('button', { name: 'Try again' }),
    );
    await within(panel).findByText('9,000');
  });

  it('does not display another account’s cached totals after an account switch', async () => {
    let complete!: (value: typeof empty) => void;
    vi.mocked(api.getGalaxyImpact)
      .mockResolvedValueOnce(populated)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            complete = resolve;
          }),
      );
    render(JournalAccountPanelTestHost);
    const panel = await screen.findByRole('region', {
      name: 'Your Galaxy Impact',
    });
    await within(panel).findByText('9,000');
    authState.set({
      ...signedIn,
      user: { ...signedIn.user!, account_id: 'account-two' },
    });
    await within(panel).findByRole('status');
    expect(within(panel).queryByText('9,000')).not.toBeInTheDocument();
    complete(empty);
    await within(panel).findByText(/Import your journals to start/i);
    expect(api.getGalaxyImpact).toHaveBeenCalledTimes(2);
  });

  it('aborts a pending private summary when its panel is removed', async () => {
    let signal!: AbortSignal;
    vi.mocked(api.getGalaxyImpact).mockImplementation((incoming) => {
      signal = incoming!;
      return new Promise(() => {});
    });
    const view = render(JournalAccountPanelTestHost);
    await waitFor(() => expect(api.getGalaxyImpact).toHaveBeenCalledOnce());
    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it('preserves withdrawal in a readable sharing disclosure without identifiers', async () => {
    const contribution: api.ContributionRow = {
      contribution_id: 'private-contribution-id',
      contribution_state: 'OFFERED',
      offered_at: '2026-09-21T12:00:00Z',
      decided_at: null,
      observation: {
        system_id64: '18446744073709551615',
        frontier_body_id: 987654,
      },
      used_in_generation: false,
    };
    vi.mocked(api.getGalaxyContributions)
      .mockResolvedValueOnce([contribution])
      .mockResolvedValueOnce([
        { ...contribution, contribution_state: 'WITHDRAWN' },
      ]);
    render(JournalAccountPanelTestHost);
    const disclosure = await screen.findByText('Manage galaxy sharing');
    expect(disclosure.tagName).toBe('SUMMARY');
    await fireEvent.click(disclosure);
    await screen.findByText(/Observation 1/);
    expect(screen.getByText(/Awaiting review/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(
      /18446744073709551615|987654|private-contribution-id|facts|Planet ID|id64/,
    );
    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Withdraw sharing for observation 1',
      }),
    );
    await waitFor(() =>
      expect(api.withdrawGalaxyContribution).toHaveBeenCalledWith(
        'private-contribution-id',
        expect.any(AbortSignal),
      ),
    );
    await screen.findByText(/Sharing withdrawn/);
  });
});
