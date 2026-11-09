import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { auth, type AuthState } from '$lib/auth/auth';
import AccountControls from './AccountControls.svelte';

vi.mock('$lib/auth/auth', async () => {
  const { writable } = await import('svelte/store');
  return {
    auth: {
      ...writable({}),
      signIn: vi.fn(),
      signOut: vi.fn(),
      bootstrap: vi.fn(),
    },
  };
});

const state = auth as typeof auth & { set: (value: AuthState) => void };
const guest: AuthState = {
  loading: false,
  authenticated: false,
  user: null,
  ownerClaimAvailable: false,
  error: null,
};

describe('shared account controls', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    state.set(guest);
  });
  afterEach(cleanup);

  it('offers guests the existing Frontier sign-in flow', async () => {
    render(AccountControls);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Sign in with Frontier' }),
    );
    expect(auth.signIn).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole('button', { name: 'Sign out' }),
    ).not.toBeInTheDocument();
  });

  it('waits for session discovery and lets a failed check retry', async () => {
    state.set({ ...guest, loading: true });
    render(AccountControls);
    expect(screen.getByRole('status')).toHaveTextContent('Checking session');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    state.set({ ...guest, error: 'offline' });
    await fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(auth.bootstrap).toHaveBeenCalledOnce();
    expect(screen.getByRole('status')).toHaveTextContent('Account unavailable');
  });

  it('links the commander to account management and prevents duplicate logout', async () => {
    state.set({
      ...guest,
      authenticated: true,
      user: {
        account_id: 'test-account',
        commander_name: 'Test Commander',
        is_owner: false,
      },
    });
    let finish!: () => void;
    vi.mocked(auth.signOut).mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    render(AccountControls);
    expect(
      screen.getByRole('link', { name: 'Test Commander' }),
    ).toHaveAttribute('href', '/account');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByRole('button', { name: 'Signing out…' })).toBeDisabled();
    expect(auth.signOut).toHaveBeenCalledOnce();
    finish();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled(),
    );
  });

  it('handles a rejected logout without an unhandled rejection', async () => {
    state.set({
      ...guest,
      authenticated: true,
      user: {
        account_id: 'test-account',
        commander_name: null,
        is_owner: true,
      },
    });
    vi.mocked(auth.signOut).mockImplementation(async () => {
      state.set({ ...guest, error: 'logout unavailable' });
      throw new Error('logout unavailable');
    });
    render(AccountControls);
    await fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent('Account unavailable');
  });
});
