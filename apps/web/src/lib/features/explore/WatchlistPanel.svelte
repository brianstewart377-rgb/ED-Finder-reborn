<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import type { WatchlistEntry } from '$lib/api/client';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import type { Id64 } from '$lib/domain/id64';
  import { usePersistenceContext } from '$lib/persistence/context';
  import type { CompareEntry, PinnedEntry } from '$lib/persistence/storage';
  import { archetypeLabel } from './archetypes';
  import {
    comparableOverallScores,
    COMPARE_MAX,
    distanceReferenceLabel,
    formatCaptureTime,
    snapshotCapturedAt,
    snapshotDistance,
    snapshotProvenanceSummary,
    snapshotScoreSummary,
  } from './compare-metrics';
  import { inspectHref, navigateWithAnchor } from './navigation';
  import {
    snapshotFromPin,
    snapshotFromWatchlist,
    toggleComparison,
    togglePin,
  } from './shortlist';
  import { createWatchlistQuery } from './watchlist-query.svelte';

  type Sort = 'recent' | 'name' | 'development' | 'distance';
  const { pins, compare } = usePersistenceContext();
  const watchlist = createWatchlistQuery();
  let pinSort = $state<Sort>('recent');
  let watchSort = $state<Sort>('recent');
  let localError = $state<string | null>(null);
  let confirmClear = $state(false);
  const watched = $derived(watchlist.query.data?.watchlist ?? []);
  const comparisonIds = $derived(
    new Set($compare.value.map((entry) => entry.id64)),
  );
  const pinnedIds = $derived(new Set($pins.value.map((entry) => entry.id64)));
  const watchedIds = $derived(
    new Set(watched.map((entry) => entry.system_id64)),
  );
  const pinScoresComparable = $derived(
    !FINDER_F4_ENABLED || comparableOverallScores($pins.value),
  );
  const watchScoresComparable = $derived(
    !FINDER_F4_ENABLED ||
      comparableOverallScores(watched.map(snapshotFromWatchlist)),
  );
  $effect(() => {
    if (FINDER_F4_ENABLED) {
      if (pinSort === 'development' && !pinScoresComparable) pinSort = 'recent';
      if (watchSort === 'development' && !watchScoresComparable)
        watchSort = 'recent';
    }
  });
  const sortedPins = $derived(
    [...$pins.value].sort((a, b) =>
      sortRows(
        a,
        b,
        pinSort === 'development' && !pinScoresComparable ? 'recent' : pinSort,
        'pinned_at',
      ),
    ),
  );
  const sortedWatchlist = $derived(
    [...watched].sort((a, b) =>
      sortRows(
        a,
        b,
        watchSort === 'development' && !watchScoresComparable
          ? 'recent'
          : watchSort,
        'added_at',
      ),
    ),
  );
  const exportHref = $derived(
    `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify($pins.value, null, 2))}`,
  );

  function numeric(value: unknown): number | null {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
  function distance(
    entry: Record<string, unknown> | WatchlistEntry,
  ): number | null {
    const coords = [numeric(entry.x), numeric(entry.y), numeric(entry.z)];
    if (!coords.every((value): value is number => value !== null)) return null;
    // (0,0,0) is the legacy placeholder for missing coordinates; only Sol truly
    // sits at the galactic origin, so treat any other all-zero row as unknown
    // rather than reporting it as 0 ly from Sol (repo coordinate contract).
    if (coords.every((value) => value === 0) && entry.name !== 'Sol')
      return null;
    return Math.hypot(...coords);
  }
  function referenceDistance(
    entry: Record<string, unknown> | WatchlistEntry,
  ): number | null {
    // Legacy pin snapshots use distance 0 as the sentinel for a galaxy-wide
    // search with no reference; treat it as unavailable (matches compare-metrics).
    const value = numeric((entry as Record<string, unknown>).distance);
    return value !== null && value > 0 ? value : null;
  }
  function populationLabel(
    entry: Record<string, unknown> | WatchlistEntry,
  ): string {
    // Zero on an inhabited system means unknown headcount, not literally zero
    // people (matches the comparison panel's population formatter).
    const row = entry as Record<string, unknown>;
    const value = numeric(row.population);
    if (value === null || value < 0) return 'Unknown';
    if (value === 0)
      return row.is_colonised === true || row.is_being_colonised === true
        ? 'Population unknown'
        : 'Uninhabited';
    return value.toLocaleString();
  }
  function score(
    entry: Record<string, unknown> | WatchlistEntry,
  ): number | null {
    if (FINDER_F4_ENABLED)
      return numeric(
        (entry as Record<string, unknown>).overall_development_potential,
      );
    return numeric(entry.archetype_score) ?? numeric(entry.score);
  }
  function dateValue(value: unknown): number {
    return typeof value === 'string' ? Date.parse(value) || 0 : 0;
  }
  function sortRows(
    a: PinnedEntry | WatchlistEntry,
    b: PinnedEntry | WatchlistEntry,
    sort: Sort,
    timestamp: 'pinned_at' | 'added_at',
  ): number {
    if (sort === 'name') return a.name.localeCompare(b.name);
    if (sort === 'development') return (score(b) ?? -1) - (score(a) ?? -1);
    if (sort === 'distance')
      return (distance(a) ?? Infinity) - (distance(b) ?? Infinity);
    return (
      dateValue((b as Record<string, unknown>)[timestamp]) -
      dateValue((a as Record<string, unknown>)[timestamp])
    );
  }
  function coordinates(entry: PinnedEntry | WatchlistEntry): string {
    return [entry.x, entry.y, entry.z]
      .map((value) => numeric(value)?.toFixed(2) ?? 'Unknown')
      .join(', ');
  }
  function label(value: unknown): string | null {
    return typeof value === 'string' && value
      ? FINDER_F4_ENABLED
        ? archetypeLabel(value)
        : value.replaceAll('_', ' ')
      : null;
  }
  function archetype(entry: PinnedEntry | WatchlistEntry): string {
    return (
      label(entry.primary_archetype) ??
      label(entry.economy_suggestion) ??
      label('economy' in entry ? entry.economy : null) ??
      'Unknown'
    );
  }
  function formattedDate(value: unknown): string {
    return typeof value === 'string' && Number.isFinite(Date.parse(value))
      ? new Date(value).toLocaleDateString()
      : 'Unknown';
  }
  function compareEntry(entry: CompareEntry): void {
    localError = toggleComparison(compare, entry).error;
  }
  function pinEntry(entry: CompareEntry): void {
    localError = togglePin(pins, entry).error;
  }
  function clearPins(): void {
    localError = pins.set([])
      ? null
      : 'Your pins could not be cleared in this browser. Try again.';
    confirmClear = false;
  }
  function followInspect(event: MouseEvent, id64: Id64): void {
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
    void navigateWithAnchor(inspectHref(id64, page.url));
  }
</script>

<section class="saved-systems" aria-labelledby="saved-systems-title">
  <div class="saved-heading">
    <h2 id="saved-systems-title">Saved systems</h2>
    <p>
      Pins and comparison selections stay in this browser. Watched systems are
      saved online using this browser's existing watchlist key.
    </p>
  </div>
  {#if localError}<p role="alert">{localError}</p>{/if}
  {#if $pins.diagnostic}
    <p role="alert">
      Your pins could not be saved or restored in this browser. Changes may not
      survive a reload.
    </p>
  {/if}

  <section aria-labelledby="pinned-systems-title">
    <p>Pin values reflect the capture time shown.</p>
    <header class="list-heading">
      <h3 id="pinned-systems-title">
        Pinned <span>({$pins.value.length})</span>
      </h3>
      <label
        >Sort pins
        <select bind:value={pinSort}>
          <option value="recent">Recently pinned</option><option value="name"
            >Name</option
          ><option value="development" disabled={!pinScoresComparable}
            >{FINDER_F4_ENABLED
              ? 'Best Colony Potential: highest first'
              : 'Development: highest first'}</option
          ><option value="distance">Distance from Sol: nearest first</option>
        </select>
      </label>
      {#if $pins.value.length > 0}
        <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- data: URI download of pins JSON, not route navigation -->
        <a class="text-action" href={exportHref} download="ed-finder-pins.json"
          >Export pins as JSON</a
        >
        <button
          class="kit-pill"
          type="button"
          onclick={() => (confirmClear = true)}>Clear all pins</button
        >
      {/if}
    </header>
    {#if FINDER_F4_ENABLED && sortedPins.length > 0 && !pinScoresComparable}
      <p>
        Score sorting is unavailable for older saves or different ranking
        versions.
      </p>
    {/if}
    {#if confirmClear && $pins.value.length > 0}
      <div
        class="clear-confirmation"
        role="group"
        aria-label="Confirm clear pins"
      >
        <p>Clear all {$pins.value.length} pinned systems?</p>
        <button class="kit-pill" type="button" onclick={clearPins}
          >Confirm clear pins</button
        >
        <button
          class="kit-pill"
          type="button"
          onclick={() => (confirmClear = false)}>Keep pins</button
        >
      </div>
    {/if}
    {#if !$pins.hydrated}
      <p role="status">Loading pinned systems…</p>
    {:else if sortedPins.length === 0}
      <p class="empty-title">No pinned systems yet</p>
      <p>Choose Pin on a Finder result to keep a shortlist here.</p>
    {:else}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll the wide table.) -->
      <div
        class="table-scroll"
        tabindex="0"
        role="region"
        aria-label="Pinned systems table"
      >
        <table>
          <caption class="sr-only">Pinned systems</caption>
          <thead
            ><tr
              ><th scope="col">System</th><th scope="col">Coords (LY)</th><th
                scope="col"
                >{FINDER_F4_ENABLED ? 'Saved scores' : 'Development'}</th
              ><th scope="col">Archetype</th><th scope="col"
                >{FINDER_F4_ENABLED ? 'Saved distance' : 'Dist. from ref.'}</th
              ><th scope="col">Captured at</th><th scope="col">Links</th><th
                scope="col">Actions</th
              ></tr
            ></thead
          >
          <tbody>
            {#each sortedPins as entry (entry.id64)}
              <tr>
                <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectHref resolves the Inspect route -->
                <th scope="row"
                  ><a
                    href={FINDER_F4_ENABLED
                      ? inspectHref(entry.id64, page.url)
                      : resolve(`/inspect?system=${entry.id64}`)}
                    onclick={FINDER_F4_ENABLED
                      ? (event) => followInspect(event, entry.id64)
                      : undefined}
                    aria-label={`Inspect ${entry.name}`}>{entry.name}</a
                  >{#if entry.is_colonised}<span class="detail-line"
                      >Colonised</span
                    >{/if}</th
                >
                <!-- eslint-enable svelte/no-navigation-without-resolve -->
                <td
                  >{coordinates(entry)}{#if distance(entry) !== null}<span
                      class="detail-line"
                      >{distance(entry)?.toFixed(1)} ly from Sol</span
                    >{/if}</td
                >
                <td>
                  {#if FINDER_F4_ENABLED}
                    {snapshotScoreSummary(entry)}
                    {#if snapshotProvenanceSummary(entry)}
                      <span class="detail-line snapshot-provenance"
                        >{snapshotProvenanceSummary(entry)}</span
                      >
                    {/if}
                  {:else}
                    {score(entry) ?? 'Unknown'}
                  {/if}
                </td>
                <td
                  >{archetype(entry)}{#if label(entry.secondary_archetype)}<span
                      class="detail-line"
                      >{label(entry.secondary_archetype)}</span
                    >{/if}</td
                >
                <td>
                  {#if FINDER_F4_ENABLED}
                    {snapshotDistance(entry) !== null
                      ? `${snapshotDistance(entry)?.toFixed(2)} LY`
                      : 'Unknown'}
                    <span class="detail-line"
                      >{distanceReferenceLabel(entry) ??
                        'Reference unknown'}</span
                    >
                  {:else}
                    {referenceDistance(entry) !== null
                      ? `${referenceDistance(entry)?.toFixed(1)} ly`
                      : 'Unknown'}
                  {/if}
                </td>
                <td>
                  {#if snapshotCapturedAt(entry)}
                    <time datetime={snapshotCapturedAt(entry)!}
                      >{formatCaptureTime(snapshotCapturedAt(entry))}</time
                    >
                  {:else}
                    Unknown
                  {/if}
                </td>
                <td
                  ><div class="row-actions">
                    <a
                      href={`https://spansh.co.uk/system/${entry.id64}`}
                      target="_blank"
                      rel="noopener noreferrer">Spansh</a
                    ><a
                      href={`https://inara.cz/starsystem/?search=${encodeURIComponent(entry.name)}`}
                      target="_blank"
                      rel="noopener noreferrer">Inara</a
                    >
                  </div></td
                >
                <td
                  ><div class="row-actions">
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Compare ${entry.name}`}
                      aria-pressed={comparisonIds.has(entry.id64)}
                      disabled={!$compare.hydrated ||
                        (!comparisonIds.has(entry.id64) &&
                          comparisonIds.size >= COMPARE_MAX)}
                      onclick={() => compareEntry(snapshotFromPin(entry))}
                      >Compare</button
                    >
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Watch ${entry.name}`}
                      disabled={!watchlist.ready ||
                        watchlist.pending ||
                        watchedIds.has(entry.id64)}
                      onclick={() => watchlist.add(entry.id64)}
                      >{watchedIds.has(entry.id64)
                        ? 'Watching'
                        : 'Watch'}</button
                    >
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Unpin ${entry.name}`}
                      onclick={() => pinEntry(snapshotFromPin(entry))}
                      >Unpin</button
                    >
                  </div></td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section
    aria-labelledby="watchlist-title"
    aria-busy={watchlist.query.isFetching}
  >
    <header class="list-heading">
      <h3 id="watchlist-title">Watchlist <span>({watched.length})</span></h3>
      <label
        >Sort watchlist
        <select bind:value={watchSort}>
          <option value="recent">Recently added</option><option value="name"
            >Name</option
          ><option value="development" disabled={!watchScoresComparable}
            >{FINDER_F4_ENABLED
              ? 'Best Colony Potential: highest first'
              : 'Development: highest first'}</option
          ><option value="distance">Distance from Sol: nearest first</option>
        </select>
      </label>
      <button
        class="kit-pill"
        type="button"
        disabled={!watchlist.ready ||
          watchlist.query.isFetching ||
          watchlist.pending}
        onclick={() => watchlist.query.refetch()}>Refresh watchlist</button
      >
    </header>
    {#if FINDER_F4_ENABLED && watched.length > 0 && !watchScoresComparable}
      <p>
        Watchlist scores have unknown ranking provenance and cannot be sorted by
        potential.
      </p>
    {/if}
    {#if watchlist.error}
      <p role="alert">{watchlist.error}</p>
      <button
        class="kit-pill"
        type="button"
        onclick={() => watchlist.retry()}
        disabled={watchlist.pending}>Retry watchlist change</button
      >
    {/if}
    {#if watchlist.query.isError}
      <p role="alert">Your watchlist could not be loaded. Try again.</p>
      <button
        class="kit-pill"
        type="button"
        onclick={() => watchlist.query.refetch()}>Retry watchlist</button
      >
    {/if}
    {#if watchlist.query.isPending}
      <p role="status">Loading watchlist…</p>
    {:else if !watchlist.query.isError && watched.length === 0}
      <p class="empty-title">No systems watched yet</p>
      <p>
        Choose Watch on a Finder result or a pinned system to follow it here.
      </p>
    {/if}
    {#if watched.length > 0}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll the wide table.) -->
      <div
        class="table-scroll"
        tabindex="0"
        role="region"
        aria-label="Watched systems table"
      >
        <table>
          <caption class="sr-only">Watched systems</caption>
          <thead
            ><tr
              ><th scope="col">System</th><th scope="col">Coords (LY)</th><th
                scope="col">Population</th
              ><th scope="col"
                >{FINDER_F4_ENABLED ? 'Saved scores' : 'Development'}</th
              ><th scope="col">Archetype</th><th scope="col">Added</th><th
                scope="col">Actions</th
              ></tr
            ></thead
          >
          <tbody>
            {#each sortedWatchlist as entry (entry.system_id64)}
              <tr>
                <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectHref resolves the Inspect route -->
                <th scope="row"
                  ><a
                    href={FINDER_F4_ENABLED
                      ? inspectHref(entry.system_id64, page.url)
                      : resolve(`/inspect?system=${entry.system_id64}`)}
                    onclick={FINDER_F4_ENABLED
                      ? (event) => followInspect(event, entry.system_id64)
                      : undefined}
                    aria-label={`Inspect ${entry.name}`}>{entry.name}</a
                  >{#if entry.is_colonised}<span class="detail-line"
                      >Colonised</span
                    >{/if}</th
                >
                <!-- eslint-enable svelte/no-navigation-without-resolve -->
                <td
                  >{coordinates(entry)}{#if distance(entry) !== null}<span
                      class="detail-line"
                      >{distance(entry)?.toFixed(1)} ly from Sol</span
                    >{/if}</td
                >
                <td>{populationLabel(entry)}</td>
                <td
                  >{FINDER_F4_ENABLED
                    ? snapshotScoreSummary(snapshotFromWatchlist(entry))
                    : (score(entry) ?? 'Unknown')}</td
                >
                <td
                  >{archetype(entry)}{#if label(entry.secondary_archetype)}<span
                      class="detail-line"
                      >{label(entry.secondary_archetype)}</span
                    >{/if}</td
                >
                <td>{formattedDate(entry.added_at)}</td>
                <td
                  ><div class="row-actions">
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Compare ${entry.name}`}
                      aria-pressed={comparisonIds.has(entry.system_id64)}
                      disabled={!$compare.hydrated ||
                        (!comparisonIds.has(entry.system_id64) &&
                          comparisonIds.size >= COMPARE_MAX)}
                      onclick={() => compareEntry(snapshotFromWatchlist(entry))}
                      >Compare</button
                    >
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Pin ${entry.name}`}
                      aria-pressed={pinnedIds.has(entry.system_id64)}
                      disabled={!$pins.hydrated}
                      onclick={() => pinEntry(snapshotFromWatchlist(entry))}
                      >{pinnedIds.has(entry.system_id64)
                        ? 'Pinned'
                        : 'Pin'}</button
                    >
                    <button
                      class="kit-pill"
                      type="button"
                      aria-label={`Remove ${entry.name} from watchlist`}
                      disabled={!watchlist.ready || watchlist.pending}
                      onclick={() => watchlist.remove(entry.system_id64)}
                      >Remove</button
                    >
                  </div></td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>
</section>

<style>
  .saved-systems {
    min-width: 0;
    border: 1px solid var(--line);
    background: var(--panel);
    background-image: var(--panel-gloss);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-panel);
  }
  .saved-heading,
  .saved-systems > section {
    padding: 1rem 1.15rem;
  }
  .saved-systems > section {
    border-top: 1px solid var(--line);
  }
  .saved-heading h2,
  h3,
  p {
    margin: 0;
  }
  p {
    margin-top: 0.6rem;
    color: var(--muted);
  }
  .saved-systems > p {
    margin: 1rem;
  }
  .list-heading,
  .row-actions,
  .clear-confirmation {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .list-heading {
    margin-bottom: 1rem;
  }
  .list-heading h3 {
    margin-right: auto;
  }
  .list-heading h3 span {
    color: var(--muted);
    font-size: 0.85em;
  }
  label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
  }
  select,
  .kit-pill,
  .text-action {
    border: 1px solid var(--line);
    background: var(--panel-raised);
    color: inherit;
    padding: 0.45rem 0.65rem;
    border-radius: var(--radius-pill);
    font: inherit;
    font-size: 0.8rem;
  }
  select {
    background: var(--panel-sunken);
    border-radius: var(--radius-sm);
  }
  button {
    cursor: pointer;
  }
  button[aria-pressed='true'] {
    border-width: 2px;
    font-weight: 700;
  }
  button:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .empty-title {
    color: inherit;
    font-weight: 600;
  }
  .table-scroll {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  th,
  td {
    padding: 0.65rem 0.5rem;
    text-align: left;
    vertical-align: top;
    border-bottom: 1px solid var(--line);
  }
  thead th {
    color: var(--muted);
    font-weight: 500;
    white-space: nowrap;
  }
  tbody th {
    min-width: 9rem;
  }
  td {
    font-variant-numeric: tabular-nums;
  }
  .detail-line {
    display: block;
    margin-top: 0.2rem;
    font-size: 0.75rem;
    color: var(--muted);
  }
  .snapshot-provenance {
    max-width: 24rem;
    overflow-wrap: anywhere;
  }
  .row-actions {
    min-width: 10rem;
  }
  .clear-confirmation {
    padding-bottom: 1rem;
  }
  .clear-confirmation p {
    margin: 0;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  @media (max-width: 640px) {
    .list-heading {
      align-items: flex-start;
    }
    label {
      flex-wrap: wrap;
    }
    .saved-heading,
    .saved-systems > section {
      padding: 0.85rem;
    }
  }
</style>
