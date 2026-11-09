<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import PlanWorkspace from '$lib/features/plan/PlanWorkspace.svelte';
  import {
    planTabUrl,
    readPlanTab,
    type PlanTab,
  } from '$lib/features/plan/tabs';
  import { parseId64, type Id64 } from '$lib/domain/id64';
  import { selectedSystem } from '$lib/persistence/stores';

  const rawSystem = $derived(page.url.searchParams.get('system'));
  const activeTab = $derived(readPlanTab(page.url.searchParams));
  function selectTab(tab: PlanTab): void {
    // Same-system navigation retains the keyed planner while updating page.url
    // and the history URL used when returning from another product route.
    // eslint-disable-next-line svelte/no-navigation-without-resolve -- cloned from the already resolved page URL
    void goto(planTabUrl(page.url, tab), {
      replaceState: true,
      keepFocus: true,
      noScroll: true,
      state: page.state,
    });
  }
  const systemId = $derived.by((): Id64 | null => {
    if (rawSystem === null) return $selectedSystem.value;
    try {
      return parseId64(rawSystem);
    } catch {
      return null;
    }
  });
  $effect(() => {
    if (
      systemId &&
      $selectedSystem.hydrated &&
      $selectedSystem.value !== systemId
    )
      selectedSystem.set(systemId);
  });
</script>

<svelte:head><title>Plan — ED-Finder V3</title></svelte:head>
<WorkspaceHeader />
{#if systemId}
  {#key systemId}<PlanWorkspace
      {systemId}
      {activeTab}
      ontabchange={selectTab}
    />{/key}
{:else}
  <main class="product-page inspect-page">
    <h1>Colony planner</h1>
    <p>
      {rawSystem !== null
        ? 'Choose a valid system ID64.'
        : 'Pick a system in Explore or Inspect first.'}
    </p>
    <nav aria-label="Choose a system">
      <a class="primary-button" href={resolve('/explore')}>Explore</a>
      <a class="secondary-button" href={resolve('/inspect')}>Inspect</a>
    </nav>
  </main>
{/if}
