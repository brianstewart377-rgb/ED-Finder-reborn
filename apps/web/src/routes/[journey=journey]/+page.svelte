<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  const names: Record<string, string> = {
    explore: 'Explore',
    inspect: 'Inspect',
    plan: 'Plan',
    review: 'Review / Export',
  };
  const title = $derived(names[page.params.journey ?? ''] ?? 'ED-Finder');
</script>

<svelte:head><title>{title} — ED-Finder V3</title></svelte:head>
<WorkspaceHeader />
<main class="placeholder">
  <a href={resolve('/')}>← ED-Finder V3</a>
  <p class="eyebrow">Migration placeholder</p>
  <h1>{title}</h1>
  <p>
    This product surface has not been ported yet. Your selected-system context
    remains available.
  </p>
</main>
