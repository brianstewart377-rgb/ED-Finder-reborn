<script lang="ts">
  import InspectPopout from './InspectPopout.svelte';
  import type { SystemDetailStation } from '$lib/api/client';
  import type { Id64 } from '$lib/domain/id64';
  import {
    AVAILABILITY_LABELS,
    sourceDate,
    type RatingsSourceState,
  } from './source-freshness';

  let {
    systemId,
    sourceUpdatedAt,
    canonicalGenerationId,
    canonicalGenerationLabel,
    ratings,
    stations,
    active = true,
  }: {
    systemId: Id64;
    sourceUpdatedAt?: string | null;
    canonicalGenerationId?: string;
    canonicalGenerationLabel?: string;
    ratings: RatingsSourceState | null;
    /** Only stations whose economies this page actually displays. */
    stations: readonly SystemDetailStation[];
    active?: boolean;
  } = $props();

  const id = $props.id();
  let openedFor = $state<Id64 | null>(null);
  const open = $derived(openedFor === systemId);
  let trigger = $state<HTMLButtonElement>();
  const catalogueDate = $derived(sourceDate(sourceUpdatedAt));
  const hasEconomies = $derived(
    stations.some((station) => station.economies.length > 0),
  );
  const latestObservation = $derived.by(() => {
    let latest: string | null = null;
    for (const station of stations) {
      const observed = station.lastObservedAt;
      if (
        observed &&
        sourceDate(observed) &&
        (latest === null || Date.parse(observed) > Date.parse(latest))
      )
        latest = observed;
    }
    return latest;
  });
  const undatedStations = $derived(
    stations.filter((station) => !sourceDate(station.lastObservedAt)).length,
  );

  function close() {
    openedFor = null;
    trigger?.focus({ preventScroll: true });
  }

  function toggle() {
    if (open) close();
    else openedFor = systemId;
  }

  $effect.pre(() => {
    if (openedFor !== null && openedFor !== systemId) openedFor = null;
  });
</script>

<button
  bind:this={trigger}
  class="sources-trigger"
  data-inspect-popout-trigger
  type="button"
  aria-haspopup="dialog"
  aria-expanded={open}
  aria-controls={open ? `${id}-dialog` : undefined}
  onclick={toggle}>Sources &amp; freshness</button
>

<InspectPopout
  id={`${id}-dialog`}
  title="Sources & freshness"
  {open}
  {active}
  {trigger}
  describedby={`${id}-help`}
  kind="sources"
  onclose={() => (openedFor = null)}
>
  <p id={`${id}-help`} class="hint">
    Each source has its own update history. The catalogue date applies only to
    catalogue facts; it does not date ratings or observations.
  </p>
  <dl class="source-products">
    <div data-source-product="canonical">
      <dt>Canonical catalogue</dt>
      <dd>
        <p>
          Bodies, stations list and coordinates from the Spansh-derived
          catalogue.
        </p>
        <p>
          <strong>Available</strong> — catalogue facts loaded for this system.
        </p>
        <p>
          Updated:
          {#if catalogueDate && sourceUpdatedAt}
            <time datetime={sourceUpdatedAt}>{catalogueDate}</time>
          {:else}
            <span data-unavailable-fields data-availability="unknown"
              >Unknown</span
            >
            — no update time supplied.
          {/if}
        </p>
        {#if canonicalGenerationLabel || canonicalGenerationId}
          <p class="source-identity">
            Generation {canonicalGenerationLabel ?? canonicalGenerationId}
            {#if canonicalGenerationLabel && canonicalGenerationId}
              ({canonicalGenerationId})
            {/if}
          </p>
        {/if}
      </dd>
    </div>
    <div data-source-product="ratings">
      <dt>Ratings V4 potentials</dt>
      <dd>
        <p>Seven economy potentials from a published derived generation.</p>
        {#if !ratings || ratings.status === 'loading'}
          <p role="status">
            <strong>Loading</strong> — retrieving published ratings.
          </p>
        {:else if ratings.status === 'available'}
          <p>
            <strong>Available</strong> — published potentials loaded for this system.
          </p>
        {:else}
          <p data-unavailable-fields data-availability={ratings.status}>
            <strong>{AVAILABILITY_LABELS[ratings.status]}</strong> —
            {ratings.status === 'unknown'
              ? 'the published source has no ratings for this system.'
              : 'the ratings request failed.'}
          </p>
          {#if ratings.status === 'unavailable'}
            <button type="button" onclick={ratings.retry}>Retry</button>
          {/if}
        {/if}
        {#if ratings?.identity}
          <p class="source-identity">
            Generation {ratings.identity.derivedGenerationId}
          </p>
          <p>
            Publication {ratings.identity.publicationSequence} · Scorer {ratings
              .identity.scorerVersion}
          </p>
          <p>
            Published: Unknown — the response supplies an identity, but no
            publication time.
          </p>
        {/if}
      </dd>
    </div>
    {#if stations.length > 0}
      <div data-source-product="station-observations">
        <dt>Station economies / observations</dt>
        <dd>
          <p>
            Economies for the stations shown on this page; observation history
            is separate from the body catalogue.
          </p>
          {#if hasEconomies}
            <p>
              <strong>Available</strong> — station economy values were returned.
            </p>
          {:else}
            <p data-unavailable-fields data-availability="unknown">
              <strong>Unknown</strong> — the source has no economy value for these
              stations.
            </p>
          {/if}
          {#if latestObservation}
            <p>
              Last observed: <time datetime={latestObservation}
                >{sourceDate(latestObservation)}</time
              >
            </p>
            <p class="hint">
              Latest dated station observation; other stations may be older.
            </p>
            {#if undatedStations > 0}
              <p class="hint">
                {undatedStations} stations have no observation time supplied.
              </p>
            {/if}
          {:else}
            <p>No observation yet</p>
            <p class="hint">
              No observation time is supplied for these stations; returned
              economy values may still be known.
            </p>
          {/if}
        </dd>
      </div>
    {/if}
    <div data-source-product="commander">
      <dt>Commander data</dt>
      <dd>
        <p>
          Population, economy, security, allegiance, government and Development
          fit.
        </p>
        <p data-unavailable-fields data-availability="not-integrated">
          <strong>Not integrated yet</strong> — ED-Finder does not read these fields
          yet.
        </p>
      </dd>
    </div>
  </dl>
</InspectPopout>

<style>
  button {
    padding: 0.35rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: var(--radius, 0.4rem);
    background: var(--panel-raised);
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .sources-trigger {
    margin-left: 0.45rem;
    font-size: 0.75rem;
    vertical-align: middle;
  }
  .hint {
    color: var(--text-muted);
  }
  .source-products {
    display: grid;
    gap: 0.75rem;
    margin: 0.8rem 0 0;
  }
  .source-products > div {
    padding-top: 0.7rem;
    border-top: 1px solid var(--line);
  }
  .source-products dt {
    margin: 0 0 0.3rem;
    color: inherit;
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: normal;
    text-transform: none;
  }
  .source-products dd {
    margin: 0;
  }
  p {
    margin: 0.25rem 0;
  }
  .source-identity {
    overflow-wrap: anywhere;
  }
</style>
