<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { LogIn, LogOut, UserRound } from '@lucide/svelte';
  import { auth } from '$lib/auth/auth';

  let signingOut = $state(false);

  async function signOut() {
    signingOut = true;
    try {
      await auth.signOut();
    } catch {
      // The auth store exposes the failure so the account controls can retry.
    } finally {
      signingOut = false;
    }
  }
</script>

<section class="account-controls" aria-label="Commander account">
  {#if $auth.loading}
    <span class="account-status" role="status">Checking session…</span>
  {:else if $auth.error}
    <span class="account-status" role="status">Account unavailable</span>
    <button
      class="quiet-button"
      type="button"
      onclick={() => void auth.bootstrap()}>Retry</button
    >
  {:else if $auth.authenticated}
    <a
      class="commander-link"
      href={resolve('/account')}
      aria-current={page.url.pathname === '/account' ? 'page' : undefined}
    >
      <UserRound size={17} aria-hidden="true" />
      <span>{$auth.user?.commander_name || 'Commander account'}</span>
    </a>
    <button
      class="quiet-button sign-out-button"
      type="button"
      onclick={() => void signOut()}
      disabled={signingOut}
    >
      <LogOut size={16} aria-hidden="true" />
      {signingOut ? 'Signing out…' : 'Sign out'}
    </button>
  {:else}
    <button
      class="primary-button sign-in-button"
      type="button"
      onclick={() => auth.signIn()}
    >
      <LogIn size={17} aria-hidden="true" />Sign in with Frontier
    </button>
  {/if}
</section>
