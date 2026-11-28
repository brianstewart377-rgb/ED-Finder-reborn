import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@ed-finder/api-client/core';
import * as api from '$lib/api/client';
import { auth, type AuthState } from '$lib/auth/auth';
import PluginKeysPanel from './PluginKeysPanel.svelte';

vi.mock('$lib/api/client', () => ({
  getPluginKeys: vi.fn(),
  makePluginKey: vi.fn(),
  cancelPluginKey: vi.fn(),
}));
vi.mock('$lib/auth/auth', async () => {
  const { writable } = await import('svelte/store');
  return { auth: { ...writable({}), signIn: vi.fn() } };
});

const made = {
  plugin_key_id: '00000000-0000-4000-8000-000000000001',
  label: 'gaming PC',
  created_at: '2026-11-28T09:30:00Z',
  last_used_at: null,
  revoked_at: null,
};
const secret = 'edfp_' + 'k'.repeat(43);

function signIn(): void {
  (auth as typeof auth & { set: (value: AuthState) => void }).set({
    authenticated: true,
    loading: false,
    user: {
      account_id: 'account-one',
      commander_name: 'Test Commander',
      is_owner: false,
    },
    ownerClaimAvailable: false,
    error: null,
  });
}

describe('keys for the EDMC plugin on the Account page', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    signIn();
  });
  afterEach(() => cleanup());

  it('shows a new key once, then lists it by name only', async () => {
    vi.mocked(api.getPluginKeys)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([made]);
    vi.mocked(api.makePluginKey).mockResolvedValue({ key: made, secret });
    render(PluginKeysPanel);
    expect(await screen.findByText('You have no keys yet.')).toBeTruthy();

    await fireEvent.input(screen.getByLabelText('Name for the new key'), {
      target: { value: ' gaming PC ' },
    });
    await fireEvent.click(screen.getByTestId('make-plugin-key'));
    expect(api.makePluginKey).toHaveBeenCalledWith('gaming PC');
    expect((await screen.findByTestId('plugin-key-secret')).textContent).toBe(
      secret,
    );
    expect(await screen.findByText('gaming PC')).toBeTruthy();

    await fireEvent.click(screen.getByText('I have copied it'));
    expect(screen.queryByTestId('plugin-key-secret')).toBeNull();
    expect(document.body.textContent).not.toContain(secret);
  });

  it('asks for a fresh sign-in when the site wants one', async () => {
    vi.mocked(api.getPluginKeys).mockResolvedValue([]);
    vi.mocked(api.makePluginKey).mockRejectedValue(
      new ApiError(403, '/api/v1/plugin/keys', {
        detail: 'Sign in again to make a key',
      }),
    );
    render(PluginKeysPanel);
    await screen.findByText('You have no keys yet.');
    await fireEvent.input(screen.getByLabelText('Name for the new key'), {
      target: { value: 'laptop' },
    });
    await fireEvent.click(screen.getByTestId('make-plugin-key'));
    expect(
      (await screen.findByTestId('plugin-keys-problem')).textContent,
    ).toContain('needs a fresh sign-in');
    expect(screen.queryByTestId('plugin-key-secret')).toBeNull();
  });

  it('cancels a key only after the member says yes', async () => {
    vi.mocked(api.getPluginKeys)
      .mockResolvedValueOnce([made])
      .mockResolvedValueOnce([]);
    vi.mocked(api.cancelPluginKey).mockResolvedValue();
    const confirm = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(PluginKeysPanel);
    const button = await screen.findByTestId(
      `cancel-plugin-key-${made.plugin_key_id}`,
    );

    await fireEvent.click(button);
    expect(api.cancelPluginKey).not.toHaveBeenCalled();

    await fireEvent.click(button);
    await waitFor(() =>
      expect(api.cancelPluginKey).toHaveBeenCalledWith(made.plugin_key_id),
    );
    expect(await screen.findByText('You have no keys yet.')).toBeTruthy();
    confirm.mockRestore();
  });

  it('says so plainly when the site is not ready for keys yet', async () => {
    vi.mocked(api.getPluginKeys).mockRejectedValue(
      new ApiError(503, '/api/v1/plugin/keys', {
        detail: 'Plugin keys are not ready on this site yet.',
      }),
    );
    render(PluginKeysPanel);
    expect(await screen.findByTestId('plugin-keys-not-ready')).toBeTruthy();
    expect(screen.queryByTestId('make-plugin-key')).toBeNull();
  });
});
