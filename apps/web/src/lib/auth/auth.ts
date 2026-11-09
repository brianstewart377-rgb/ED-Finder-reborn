import { writable, derived, type Readable } from 'svelte/store';
import { browser } from '$app/environment';
import {
  authLogout,
  claimOwner,
  frontierLoginUrl,
  getAuthSession,
} from '$lib/api/client';
import { adminToken } from '$lib/persistence/stores';

export interface AuthUser {
  account_id: string;
  commander_name: string | null;
  is_owner: boolean;
}
export interface AuthState {
  loading: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  ownerClaimAvailable: boolean;
  error: string | null;
}
export interface AuthApi {
  session: typeof getAuthSession;
  logout: () => ReturnType<typeof getAuthSession>;
  claimOwner: (adminToken: string) => ReturnType<typeof getAuthSession>;
}
export type AuthTokenStore = Pick<typeof adminToken, 'set' | 'clear'>;

const empty: AuthState = {
  loading: false,
  authenticated: false,
  user: null,
  ownerClaimAvailable: false,
  error: null,
};

export function createAuthStore(
  api: AuthApi = { session: getAuthSession, logout: authLogout, claimOwner },
  tokenStore: AuthTokenStore = adminToken,
) {
  const state = writable<AuthState>({ ...empty, loading: browser });
  let operationGeneration = 0;
  const accept = (session: Awaited<ReturnType<AuthApi['session']>>) =>
    state.set({
      loading: false,
      authenticated: session.authenticated,
      user: session.user
        ? {
            account_id: session.user.account_id,
            commander_name: session.user.commander_name ?? null,
            is_owner: session.user.is_owner,
          }
        : null,
      ownerClaimAvailable: session.owner_claim_available ?? false,
      error: null,
    });
  const fail = (error: unknown) =>
    state.set({
      ...empty,
      error: error instanceof Error ? error.message : String(error),
    });
  const reportError = (error: unknown) =>
    state.update((value) => ({
      ...value,
      loading: false,
      error: error instanceof Error ? error.message : String(error),
    }));
  return {
    subscribe: state.subscribe,
    owner: derived(
      state,
      ($state) => $state.user?.is_owner === true,
    ) as Readable<boolean>,
    async bootstrap() {
      const generation = ++operationGeneration;
      state.update((value) => ({ ...value, loading: true, error: null }));
      try {
        const session = await api.session();
        if (generation === operationGeneration) accept(session);
      } catch (error) {
        if (generation === operationGeneration) fail(error);
      }
    },
    signIn() {
      if (!browser) return;
      const returnTo = `${location.pathname}${location.search}${location.hash}`;
      location.assign(frontierLoginUrl(returnTo));
    },
    async signOut() {
      const generation = ++operationGeneration;
      state.update((value) => ({ ...value, error: null }));
      const tokenClearError = tokenStore.clear()
        ? null
        : new Error('The session token could not be cleared in this browser.');
      try {
        const session = await api.logout();
        if (generation !== operationGeneration) return;
        if (tokenClearError) throw tokenClearError;
        accept(session);
        if (browser && ['/admin', '/operator'].includes(location.pathname))
          location.replace('/');
      } catch (error) {
        const failure = tokenClearError ?? error;
        if (generation === operationGeneration) fail(failure);
        throw failure;
      }
    },
    async claimOwner(token: string) {
      const trimmed = token.trim();
      if (!trimmed) throw new Error('Admin token is required');
      const generation = ++operationGeneration;
      state.update((value) => ({ ...value, error: null }));
      let session: Awaited<ReturnType<AuthApi['claimOwner']>>;
      try {
        session = await api.claimOwner(trimmed);
      } catch (error) {
        // A rejected one-time owner claim does not invalidate the cookie-backed
        // Frontier session that was required to make the attempt.
        if (generation === operationGeneration) reportError(error);
        throw error;
      }
      if (generation !== operationGeneration) return;
      if (!tokenStore.set(trimmed)) {
        accept(session);
        const error = new Error(
          'The owner was linked, but the session token could not be stored in this browser.',
        );
        reportError(error);
        throw error;
      }
      accept(session);
    },
  };
}

export const auth = createAuthStore();
