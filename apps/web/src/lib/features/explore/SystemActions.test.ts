import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writable, get } from 'svelte/store';
import type { AuthState } from '$lib/auth/auth';
import { getWatchlist, addWatchlist, removeWatchlist } from '$lib/api/client';
import { queryClient } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import { pins, compare, syncKey } from '$lib/persistence/stores';
import SystemActionsTestHost from './SystemActionsTestHost.svelte';

vi.mock('$lib/auth/auth', () => ({
  auth: writable<AuthState>({
    loading: false,
    authenticated: false,
    user: null,
    ownerClaimAvailable: false,
    error: null,
  }),
}));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getWatchlist: vi.fn(),
  addWatchlist: vi.fn(),
  removeWatchlist: vi.fn(),
}));

const key = 'test-watchlist-sync-key';
const system = {
  id64: parseId64('9007199254740993'),
  name: 'Lave',
  elw_count: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  localStorage.clear();
  pins.set([]);
  compare.set([]);
  syncKey.set({ state: { syncKey: key }, version: 0 });
  vi.mocked(getWatchlist).mockResolvedValue({ sync_key: key, watchlist: [] });
  vi.mocked(addWatchlist).mockResolvedValue({ ok: true, sync_key: key });
  vi.mocked(removeWatchlist).mockResolvedValue({ ok: true });
});
afterEach(cleanup);

describe('Finder system actions', () => {
  it('pins and compares a Finder snapshot using the existing stores', async () => {
    render(SystemActionsTestHost, { system });
    await fireEvent.click(screen.getByRole('button', { name: 'Pin Lave' }));
    expect(get(pins).value[0]).toMatchObject(system);
    expect(screen.getByRole('button', { name: 'Unpin Lave' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Compare Lave' }));
    expect(get(compare).value[0]).toMatchObject(system);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Remove Lave from comparison' }),
    );
    expect(get(compare).value).toEqual([]);
  });

  it('adds a watch through the API and reflects the refreshed list', async () => {
    render(SystemActionsTestHost, { system });
    const watch = screen.getByRole('button', { name: 'Watch Lave' });
    await waitFor(() => expect(watch).toBeEnabled());
    vi.mocked(getWatchlist).mockResolvedValue({
      sync_key: key,
      watchlist: [
        {
          system_id64: system.id64,
          name: 'Lave',
          added_at: '2026-09-21T00:00:00Z',
        },
      ],
    });
    await fireEvent.click(watch);
    await waitFor(() => expect(addWatchlist).toHaveBeenCalled());
    expect(vi.mocked(addWatchlist).mock.calls[0].slice(0, 2)).toEqual([
      key,
      system.id64,
    ]);
    expect(
      await screen.findByRole('button', { name: 'Stop watching Lave' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps local actions available while a watch request fails and supports retry', async () => {
    vi.mocked(addWatchlist).mockRejectedValueOnce(
      new Error('private backend detail'),
    );
    render(SystemActionsTestHost, { system });
    const watch = screen.getByRole('button', { name: 'Watch Lave' });
    await waitFor(() => expect(watch).toBeEnabled());
    await fireEvent.click(watch);
    expect(await screen.findByRole('alert')).not.toHaveTextContent(
      'private backend detail',
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Pin Lave' }));
    expect(get(pins).value).toHaveLength(1);
    await fireEvent.click(watch);
    await waitFor(() => expect(addWatchlist).toHaveBeenCalledTimes(2));
  });
});
