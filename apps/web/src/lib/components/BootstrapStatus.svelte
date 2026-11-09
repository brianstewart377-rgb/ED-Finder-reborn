<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { getHealth } from '$lib/api/client';
  import { auth } from '$lib/auth/auth';

  const health = createQuery(() => ({
    queryKey: ['bootstrap', 'health'],
    queryFn: ({ signal }) => getHealth(signal),
  }));
</script>

<section class="status-panel" aria-label="Connection status">
  <p class="connection-label">Connection status</p>
  <dl>
    <div>
      <dt>Catalogue connection</dt>
      <dd aria-live="polite">
        {#if health.isPending}Checking…{:else if health.isError}Unavailable{:else}{health
            .data.database === 'connected'
            ? 'Connected'
            : health.data.status}{/if}
      </dd>
    </div>
    <div>
      <dt>Commander</dt>
      <dd aria-live="polite">
        {#if $auth.loading}Checking…{:else if $auth.error}Unavailable{:else if $auth.authenticated}{$auth
            .user?.commander_name ?? 'Signed in'}{:else}Guest{/if}
      </dd>
    </div>
  </dl>
</section>
