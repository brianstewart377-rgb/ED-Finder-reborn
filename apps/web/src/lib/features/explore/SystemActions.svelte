<script lang="ts">
  import type { ExploreSystem } from '$lib/api/client';
  import { usePersistenceContext } from '$lib/persistence/context';
  import {
    snapshotFromExplore,
    toggleComparison,
    togglePin,
  } from './shortlist';
  import { createWatchlistQuery } from './watchlist-query.svelte';

  let { system } = $props<{ system: ExploreSystem }>();
  const { pins, compare } = usePersistenceContext();
  const watches = createWatchlistQuery();
  const snapshot = $derived(snapshotFromExplore(system));
  const pinned = $derived(
    $pins.value.some((entry) => entry.id64 === system.id64),
  );
  const compared = $derived(
    $compare.value.some((entry) => entry.id64 === system.id64),
  );
  const watched = $derived(
    watches.query.data?.watchlist.some(
      (entry) => entry.system_id64 === system.id64,
    ) ?? false,
  );
  let localError = $state<string | null>(null);
</script>

<div class="system-actions" role="group" aria-label={`Save ${snapshot.name}`}>
  <button
    type="button"
    class="quiet-button"
    aria-label={`${pinned ? 'Unpin' : 'Pin'} ${snapshot.name}`}
    aria-pressed={pinned}
    disabled={!$pins.hydrated}
    onclick={() => {
      localError = togglePin(pins, snapshot).error;
    }}
  >
    {pinned ? 'Pinned' : 'Pin'}
  </button>
  <button
    type="button"
    class="quiet-button"
    aria-label={compared
      ? `Remove ${snapshot.name} from comparison`
      : `Compare ${snapshot.name}`}
    aria-pressed={compared}
    disabled={!$compare.hydrated}
    onclick={() => {
      localError = toggleComparison(compare, snapshot).error;
    }}
  >
    {compared ? 'Comparing' : 'Compare'}
  </button>
  <button
    type="button"
    class="quiet-button"
    aria-label={watched
      ? `Stop watching ${snapshot.name}`
      : `Watch ${snapshot.name}`}
    aria-pressed={watched}
    disabled={!watches.ready || watches.pending || !watches.query.data}
    onclick={() =>
      watched ? watches.remove(system.id64) : watches.add(system.id64)}
  >
    {watched ? 'Watching' : 'Watch'}
  </button>
  {#if localError || watches.error}<p role="alert">
      {localError ?? watches.error}
    </p>{/if}
</div>

<style>
  .system-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    align-items: center;
  }
  button {
    padding: 0.35rem 0.65rem;
    font-size: 0.8rem;
  }
  button[aria-pressed='true'] {
    border-color: currentColor;
    font-weight: 700;
  }
  p {
    flex-basis: 100%;
    margin: 0.25rem 0;
    font-size: 0.8rem;
  }
</style>
