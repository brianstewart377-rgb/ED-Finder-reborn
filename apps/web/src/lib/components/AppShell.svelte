<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { auth } from '$lib/auth/auth';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { parseId64, type Id64 } from '$lib/domain/id64';
  import {
    inspectHref,
    navigateWithAnchor,
  } from '$lib/features/explore/navigation';
  import { usePersistenceContext } from '$lib/persistence/context';
  import { hydrateApplicationStores } from '$lib/persistence/stores';
  import { applyLegacyHash } from '$lib/routing/legacy-hash';
  import SystemOverlay from './SystemOverlay.svelte';

  let { children } = $props<{ children: import('svelte').Snippet }>();
  const persistence = usePersistenceContext();
  const { selectedSystem } = persistence;
  let persistenceReady = $state(false);
  let lastUrlSelection: Id64 | null = null;

  const detailId = $derived.by((): Id64 | null => {
    const raw = page.url.searchParams.get('system');
    if (!raw) return null;
    try {
      return parseId64(raw);
    } catch {
      return null;
    }
  });

  const exploreSelection = $derived(
    FINDER_F4_ENABLED && page.url.pathname === '/explore',
  );
  const urlSelection = $derived.by((): Id64 | null => {
    if (!exploreSelection) return detailId;
    const values = page.url.searchParams.getAll('selected');
    if (values.length !== 1) return null;
    try {
      return parseId64(values[0]);
    } catch {
      return null;
    }
  });

  // Inspect is the canonical detail route. The modal lifecycle is retained
  // only as an Explore-context affordance, never as a competing route model.
  let overlayId = $derived(page.url.pathname === '/explore' ? detailId : null);

  function establishUrlSelection(): void {
    if (!FINDER_F4_ENABLED) {
      // Keep main's detail-driven latch, without reading the F4 selection
      // derived or subscribing to the persisted store via get().
      if (!detailId) {
        lastUrlSelection = null;
        return;
      }
      if (detailId === lastUrlSelection) return;
      selectedSystem.set(detailId);
      lastUrlSelection = detailId;
      return;
    }
    // F4 Explore owns even an absent selection. Other routes keep the
    // detail-driven selection without clearing on omission.
    if (!urlSelection && !exploreSelection) return;
    if (get(selectedSystem).value !== urlSelection)
      selectedSystem.set(urlSelection);
  }

  function followInspect(event: MouseEvent, id64: Id64): void {
    if (
      !exploreSelection ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    void navigateWithAnchor(inspectHref(id64, page.url));
  }

  $effect(() => {
    if (persistenceReady) establishUrlSelection();
  });

  onMount(() => {
    hydrateApplicationStores();
    establishUrlSelection();
    persistenceReady = true;

    const normaliseLegacyHash = () =>
      applyLegacyHash(location, (url) => {
        // eslint-disable-next-line svelte/no-navigation-without-resolve -- compatibility destination is closed and validated
        void goto(url, { replaceState: true });
      });
    normaliseLegacyHash();
    window.addEventListener('hashchange', normaliseLegacyHash);
    void auth.bootstrap();

    return () => window.removeEventListener('hashchange', normaliseLegacyHash);
  });
</script>

<a class="skip-link" href="#main-content">Skip to main content</a>
{#if persistenceReady && $selectedSystem.hydrated && $selectedSystem.value}
  <aside class="context-chip" data-testid="selected-system-context">
    Selected system <strong>{$selectedSystem.value}</strong>
    <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectHref resolves the Inspect route -->
    <a
      href={exploreSelection
        ? inspectHref($selectedSystem.value, page.url)
        : resolve(`/inspect?system=${$selectedSystem.value}`)}
      onclick={FINDER_F4_ENABLED
        ? (event) => followInspect(event, $selectedSystem.value!)
        : undefined}>Inspect</a
    >
    <!-- eslint-enable svelte/no-navigation-without-resolve -->
    <a href={resolve(`/plan?system=${$selectedSystem.value}`)}>Open plan</a>
  </aside>
{/if}
<div id="main-content" tabindex="-1">{@render children()}</div>
{#if overlayId}<SystemOverlay id64={overlayId} />{/if}
