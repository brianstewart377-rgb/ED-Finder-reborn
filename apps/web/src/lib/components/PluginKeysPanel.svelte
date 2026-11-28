<script lang="ts">
  // Keys for ED-Finder's EDMC plugin ("Haul — design", step 2a). A key is
  // shown once, straight after it is made; the site keeps only a scrambled
  // copy, so it can never show it again.
  import { onDestroy } from 'svelte';
  import { ApiError } from '@ed-finder/api-client/core';
  import {
    cancelPluginKey,
    getPluginKeys,
    makePluginKey,
    type PluginKey,
  } from '$lib/api/client';
  import { auth } from '$lib/auth/auth';

  let keys = $state<readonly PluginKey[]>([]);
  let loading = $state(false);
  let busy = $state<string | null>(null);
  let problem = $state<string | null>(null);
  let notReady = $state(false);
  let label = $state('');
  let shown = $state<{ label: string; secret: string } | null>(null);
  let copied = $state(false);
  let wasAuthenticated = false;
  let generation = 0;

  const live = $derived(keys.filter((key) => !key.revoked_at));

  function when(value: string | null | undefined, never: string): string {
    if (!value) return never;
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return never;
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  }

  function explain(cause: unknown): string {
    if (cause instanceof ApiError) {
      const detail =
        cause.body && typeof cause.body === 'object' && 'detail' in cause.body
          ? (cause.body as { detail: unknown }).detail
          : null;
      if (cause.status === 403 && detail === 'Sign in again to make a key')
        return 'For safety, making a key needs a fresh sign-in. Sign out, sign in again, and then make the key.';
      if (typeof detail === 'string') return detail;
    }
    return cause instanceof Error ? cause.message : String(cause);
  }

  async function refresh(): Promise<void> {
    const mine = ++generation;
    loading = true;
    problem = null;
    try {
      const next = await getPluginKeys();
      if (mine === generation) {
        keys = next;
        notReady = false;
      }
    } catch (cause) {
      if (mine !== generation) return;
      if (cause instanceof ApiError && cause.status === 503) notReady = true;
      else problem = explain(cause);
    } finally {
      if (mine === generation) loading = false;
    }
  }

  async function make(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    busy = 'make';
    problem = null;
    copied = false;
    try {
      const made = await makePluginKey(label.trim());
      shown = { label: made.key.label, secret: made.secret };
      label = '';
      await refresh();
    } catch (cause) {
      problem = explain(cause);
    } finally {
      busy = null;
    }
  }

  async function copy(): Promise<void> {
    if (!shown) return;
    try {
      await navigator.clipboard.writeText(shown.secret);
      copied = true;
    } catch {
      problem =
        'Copying did not work here; select the key and copy it by hand.';
    }
  }

  async function cancel(key: PluginKey): Promise<void> {
    if (
      !window.confirm(
        `Cancel the key "${key.label}"? The plugin using it stops sending at once.`,
      )
    )
      return;
    busy = key.plugin_key_id;
    problem = null;
    try {
      await cancelPluginKey(key.plugin_key_id);
      await refresh();
    } catch (cause) {
      problem = explain(cause);
    } finally {
      busy = null;
    }
  }

  $effect(() => {
    const authenticated = $auth.authenticated;
    if (!authenticated) {
      wasAuthenticated = false;
      keys = [];
      shown = null;
      return;
    }
    if (!wasAuthenticated) {
      wasAuthenticated = true;
      void refresh();
    }
  });

  onDestroy(() => {
    generation += 1;
    shown = null;
  });
</script>

{#if $auth.authenticated}
  <section class="identity-panel" aria-labelledby="plugin-keys-heading">
    <div class="identity-panel-heading">
      <div>
        <p class="eyebrow">Haul</p>
        <h2 id="plugin-keys-heading">Keys for the EDMC plugin</h2>
      </div>
    </div>
    <p class="identity-panel-copy">
      ED-Finder's plugin for EDMC sends your construction sites' shopping lists
      to your account, and shows the site you are docked at in EDMC's own
      window. It needs a key: make one here and paste it into the plugin's
      settings. Make one key for each PC.
    </p>
    <ol class="plugin-steps" data-testid="plugin-steps">
      <!-- eslint-disable svelte/no-navigation-without-resolve -- the API's download, not a page of the app -->
      <li>
        <a href="/api/v1/plugin/download" download data-testid="plugin-download"
          >Download the plugin</a
        > and unzip it.
      </li>
      <!-- eslint-enable svelte/no-navigation-without-resolve -->
      <li>
        In EDMC open File, Settings, Plugins, press Open, and put the
        <code>EDFinder</code> folder there. Restart EDMC.
      </li>
      <li>
        Make a key below. In EDMC's Settings, open the ED-Finder tab and paste
        it.
      </li>
      <li>
        With the game running, press Test connection there, then Send past
        journals once.
      </li>
    </ol>
    {#if notReady}
      <p role="status" data-testid="plugin-keys-not-ready">
        Plugin keys are not ready on this site yet.
      </p>
    {:else}
      {#if shown}
        <div
          class="plugin-key-shown"
          role="status"
          data-testid="plugin-key-shown"
        >
          <p>
            <strong>Your new key "{shown.label}".</strong> Copy it now: it is shown
            only this once, and the site cannot show it again.
          </p>
          <code class="plugin-key-secret" data-testid="plugin-key-secret"
            >{shown.secret}</code
          >
          <div class="plugin-key-actions">
            <button
              type="button"
              class="secondary-button"
              onclick={() => void copy()}
              >{copied ? 'Copied' : 'Copy the key'}</button
            >
            <button
              type="button"
              class="quiet-button"
              onclick={() => (shown = null)}>I have copied it</button
            >
          </div>
        </div>
      {/if}
      <form class="plugin-key-form" onsubmit={(event) => void make(event)}>
        <label for="plugin-key-label">Name for the new key</label>
        <input
          id="plugin-key-label"
          type="text"
          maxlength="60"
          placeholder="gaming PC"
          bind:value={label}
          disabled={busy !== null}
        />
        <button
          type="submit"
          class="secondary-button"
          disabled={busy !== null || !label.trim()}
          data-testid="make-plugin-key"
          >{busy === 'make' ? 'Making…' : 'Make a key'}</button
        >
      </form>
      {#if problem}
        <p role="alert" data-testid="plugin-keys-problem">{problem}</p>
      {/if}
      {#if loading && keys.length === 0}
        <p aria-live="polite">Loading your keys…</p>
      {:else if live.length === 0}
        <p class="state-copy">You have no keys yet.</p>
      {:else}
        <ul class="identity-list" data-testid="plugin-keys">
          {#each live as key (key.plugin_key_id)}
            <li>
              <div>
                <strong>{key.label}</strong>
                <small
                  >Made {when(key.created_at, 'recently')} · last used {when(
                    key.last_used_at,
                    'never',
                  )}</small
                >
              </div>
              <button
                type="button"
                class="quiet-button"
                onclick={() => void cancel(key)}
                disabled={busy !== null}
                aria-label={`Cancel the key ${key.label}`}
                data-testid={`cancel-plugin-key-${key.plugin_key_id}`}
                >{busy === key.plugin_key_id ? 'Cancelling…' : 'Cancel'}</button
              >
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </section>
{/if}

<style>
  .plugin-key-shown {
    border: 1px solid var(--border-strong, currentColor);
    border-radius: 6px;
    padding: 0.75rem;
    margin-block: 0.75rem;
  }
  .plugin-key-secret {
    display: block;
    overflow-wrap: anywhere;
    user-select: all;
    margin-block: 0.5rem;
  }
  .plugin-key-actions,
  .plugin-key-form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
  }
</style>
