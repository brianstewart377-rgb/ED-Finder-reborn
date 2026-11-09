<script lang="ts">
  import { replaceState } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import SystemDetail from '$lib/components/SystemDetail.svelte';
  import {
    parseInspectTab,
    type InspectTab,
  } from '$lib/components/inspect-tabs';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { parseId64, type Id64 } from '$lib/domain/id64';
  import { navigateWithAnchor } from '$lib/features/explore/navigation';

  const rawSystem = $derived(page.url.searchParams.get('system'));
  const tab = $derived(parseInspectTab(page.url.searchParams.get('tab')));

  function changeTab(next: InspectTab): void {
    const url = new URL(page.url);
    url.searchParams.set('tab', next);
    // Shallow URL state: retain mounted queries, focus, scroll and Finder context.
    // eslint-disable-next-line svelte/no-navigation-without-resolve -- the current page URL already contains the resolved base path
    replaceState(url, page.state);
  }
  const systemId = $derived.by((): Id64 | null => {
    if (!rawSystem) return null;
    try {
      return parseId64(rawSystem);
    } catch {
      return null;
    }
  });
  const backHref = $derived(FINDER_F4_ENABLED ? exploreHref(false) : null);
  const openHref = $derived(FINDER_F4_ENABLED ? exploreHref(true) : null);

  function exploreHref(selectSystem: boolean): string {
    const params = new URLSearchParams(page.url.searchParams.get('from') ?? '');
    if (selectSystem && systemId) {
      // Replace prior selections, including every duplicate in the source query.
      params.delete('selected');
      params.delete('system');
      params.set('selected', systemId);
      params.set('system', systemId);
    }
    const query = params.toString();
    return `${resolve('/explore')}${query ? `?${query}` : ''}`;
  }

  function followExplore(event: MouseEvent, destination: string): void {
    if (
      !FINDER_F4_ENABLED ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    void navigateWithAnchor(destination);
  }
</script>

<svelte:head><title>Inspect — ED-Finder V3</title></svelte:head>
<WorkspaceHeader />
<main class="inspect-page product-page">
  {#if systemId}
    <!-- The actions sit above the system card, as in the concept: Back to
         Explore at the left, the forward actions at the right. -->
    <nav class="detail-actions inspect-actions" aria-label="System actions">
      <!-- eslint-disable svelte/no-navigation-without-resolve -- exploreHref resolves the Explore route -->
      <a
        class="secondary-button"
        href={FINDER_F4_ENABLED ? backHref! : resolve('/explore')}
        onclick={FINDER_F4_ENABLED
          ? (event) => followExplore(event, backHref!)
          : undefined}>Back to Explore</a
      >
      <span class="inspect-actions-end">
        <a
          class="secondary-button"
          href={FINDER_F4_ENABLED
            ? openHref!
            : resolve(`/explore?system=${systemId}`)}
          onclick={FINDER_F4_ENABLED
            ? (event) => followExplore(event, openHref!)
            : undefined}>Open in Explore</a
        >
        <!-- eslint-enable svelte/no-navigation-without-resolve -->
        <a class="primary-button" href={resolve(`/plan?system=${systemId}`)}
          >Continue to Plan</a
        >
      </span>
    </nav>
    <SystemDetail
      id64={systemId}
      {tab}
      onTabChange={changeTab}
      autofocusHeading
    />
  {:else}
    <section
      class="system-detail invalid"
      data-invalid-system={rawSystem ?? ''}
    >
      <p class="eyebrow">Inspect</p>
      <h1>Choose a valid system</h1>
      <p class="state-copy" role="alert">
        {#if rawSystem}
          <code>{rawSystem}</code> is not a canonical unsigned 64-bit system identifier.
        {:else}
          Inspect opens a system by its exact ID64. Start in Explore to choose
          one.
        {/if}
      </p>
      <a class="primary-button" href={resolve('/explore')}>Explore systems</a>
    </section>
  {/if}
</main>

<style>
  .inspect-actions {
    justify-content: space-between;
    margin: 0 0 1rem;
  }

  .inspect-actions-end {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
  }

  @media (max-width: 700px) {
    .inspect-actions-end {
      flex: 1 1 100%;
    }

    .inspect-actions-end a {
      flex: 1 1 100%;
    }
  }
</style>
