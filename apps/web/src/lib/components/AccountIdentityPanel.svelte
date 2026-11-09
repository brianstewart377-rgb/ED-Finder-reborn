<script lang="ts">
  import { onDestroy } from 'svelte';
  import {
    getAuthIdentities,
    startFrontierLink,
    unlinkAuthIdentity,
    type AuthIdentity,
  } from '$lib/api/client';
  import { auth } from '$lib/auth/auth';

  let identities = $state<readonly AuthIdentity[]>([]);
  let loading = $state(false);
  let actionId = $state<string | null>(null);
  let error = $state<string | null>(null);
  let wasAuthenticated = false;
  let refreshGeneration = 0;

  function identityLabel(identity: AuthIdentity): string {
    const suffix = identity.external_identity_id.slice(-8);
    return `${identity.provider} identity · …${suffix}`;
  }

  function linkedAt(identity: AuthIdentity): string {
    const date = new Date(identity.linked_at);
    if (Number.isNaN(date.valueOf())) return 'Linked recently';
    return `Linked ${new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
    }).format(date)}`;
  }

  async function refresh(): Promise<void> {
    const generation = ++refreshGeneration;
    loading = true;
    error = null;
    try {
      const next = await getAuthIdentities();
      if (generation === refreshGeneration) identities = next;
    } catch (cause) {
      if (generation === refreshGeneration)
        error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (generation === refreshGeneration) loading = false;
    }
  }

  async function linkIdentity(): Promise<void> {
    actionId = 'link';
    error = null;
    try {
      const returnTo = `${location.pathname}${location.search}${location.hash}`;
      const authorizationUrl = await startFrontierLink(returnTo);
      location.assign(authorizationUrl);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
      actionId = null;
    }
  }

  async function unlinkIdentity(identity: AuthIdentity): Promise<void> {
    if (identities.length <= 1) return;
    const label = identityLabel(identity);
    if (
      !window.confirm(`Unlink ${label}? You can link it again with Frontier.`)
    )
      return;
    actionId = identity.external_identity_id;
    error = null;
    try {
      await unlinkAuthIdentity(identity.external_identity_id);
      await auth.bootstrap();
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      actionId = null;
    }
  }

  $effect(() => {
    const authenticated = $auth.authenticated;
    if (!authenticated) {
      wasAuthenticated = false;
      identities = [];
      return;
    }
    if (!wasAuthenticated) {
      wasAuthenticated = true;
      void refresh();
    }
  });

  onDestroy(() => {
    refreshGeneration += 1;
  });
</script>

{#if $auth.authenticated}
  <section class="identity-panel" aria-labelledby="identity-panel-heading">
    <div class="identity-panel-heading">
      <div>
        <p class="eyebrow">Account access</p>
        <h2 id="identity-panel-heading">Linked Frontier identities</h2>
      </div>
      <button
        type="button"
        class="secondary-button"
        onclick={() => void linkIdentity()}
        disabled={actionId !== null}
        data-testid="link-frontier-identity"
        >{actionId === 'link'
          ? 'Opening Frontier…'
          : 'Link another identity'}</button
      >
    </div>
    <p class="identity-panel-copy">
      Keep more than one Frontier identity linked so you can sign in from either
      account. A recent authentication is required for link and unlink actions.
    </p>
    {#if loading}
      <p aria-live="polite">Loading linked identities…</p>
    {:else if error}
      <p role="alert">{error}</p>
    {:else}
      <ul class="identity-list">
        {#each identities as identity (identity.external_identity_id)}
          <li>
            <div>
              <strong>{identityLabel(identity)}</strong>
              <small>{linkedAt(identity)}</small>
            </div>
            <button
              type="button"
              class="quiet-button"
              onclick={() => void unlinkIdentity(identity)}
              disabled={identities.length <= 1 || actionId !== null}
              aria-label={`Unlink ${identityLabel(identity)}`}
              data-testid={`unlink-frontier-identity-${identity.external_identity_id}`}
              >{actionId === identity.external_identity_id
                ? 'Unlinking…'
                : 'Unlink'}</button
            >
          </li>
        {/each}
      </ul>
      <p class="identity-panel-note">
        {#if identities.length <= 1}
          The last active login identity cannot be unlinked.
        {:else}
          Unlinking disables that identity; a later direct login stays denied
          until you explicitly link it again.
        {/if}
      </p>
    {/if}
  </section>
{/if}
