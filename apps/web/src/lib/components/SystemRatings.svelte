<script lang="ts">
  import { createQuery, useQueryClient } from '@tanstack/svelte-query';
  import {
    ApiError,
    getSystemRatingExplanation,
    getSystemRatings,
    type EconomyRatingExplanation,
    type SystemDetailBody,
  } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import RatingExplanation from './RatingExplanation.svelte';
  import InspectPopout from './InspectPopout.svelte';
  import {
    AVAILABILITY_LABELS,
    type RatingsSourceState,
  } from './source-freshness';
  import {
    byPotential,
    percent,
    ratingEconomyLabel,
  } from './system-detail-format';

  type Props = {
    id64: Id64;
    bodies: readonly SystemDetailBody[];
    headingId: string;
    active?: boolean;
    onSourceChange?: (source: RatingsSourceState) => void;
  };
  let {
    id64,
    bodies,
    headingId,
    active = true,
    onSourceChange,
  }: Props = $props();
  let trigger = $state<HTMLButtonElement>();

  const queryClient = useQueryClient();

  /**
   * The economy whose explanation is open, one at a time. It belongs to the
   * system it was opened on: Inspect and the Explore overlay keep this
   * component mounted when the system changes, and an explanation must not
   * reopen (and fetch) for the next system on its own.
   */
  let opened = $state<Readonly<{ id64: Id64; economy: string }> | null>(null);
  const open = $derived(
    opened !== null && opened.id64 === id64 ? opened.economy : null,
  );

  // 404: not in the published ratings generation. 422: an id64 beyond the
  // signed 64-bit key space, which therefore cannot be rated.
  const isAbsent = (error: unknown): boolean =>
    error instanceof ApiError && (error.status === 404 || error.status === 422);

  const ratings = createQuery(() => ({
    queryKey: queryKeys.systemRatings(id64),
    queryFn: ({ signal }) => getSystemRatings(id64, signal),
    retry: (failureCount, error) => !isAbsent(error) && failureCount < 1,
  }));
  const identity = $derived(ratings.data?.identity ?? null);
  const availability = $derived(
    ratings.isError
      ? isAbsent(ratings.error)
        ? 'unknown'
        : 'unavailable'
      : ratings.isPending
        ? 'loading'
        : 'available',
  );

  $effect(() => {
    const systemId = id64;
    onSourceChange?.({
      id64: systemId,
      status: availability,
      identity,
      // Capture the emitting system so a detached callback cannot refetch the
      // next selection. This reuses the mounted query and its request.
      retry: () => {
        void queryClient.refetchQueries({
          queryKey: queryKeys.systemRatings(systemId),
          exact: true,
        });
      },
    });
  });
  // Lazy: nothing is fetched until a "Why?" is opened, and the key carries the
  // identity of the ratings on screen so a republish never reuses it.
  const explanation = createQuery(() => ({
    queryKey: queryKeys.systemRatingExplanation(id64, identity),
    queryFn: ({ signal }) => {
      if (!identity)
        throw new Error('Ratings must load before their explanation');
      return getSystemRatingExplanation(id64, identity, signal);
    },
    enabled: open !== null && identity !== null,
    retry: (failureCount, error) => !isAbsent(error) && failureCount < 1,
  }));

  const ordered = $derived(
    ratings.data ? byPotential(ratings.data.ratings) : [],
  );
  const bodyNames = $derived(
    new Map<string, string>(bodies.map((body) => [body.bodyKey, body.name])),
  );
  const explained = $derived.by((): EconomyRatingExplanation | null => {
    const data = explanation.data;
    if (open === null || data?.kind !== 'explained') return null;
    return data.economies.find((economy) => economy.economy === open) ?? null;
  });

  function toggle(economy: string, button: HTMLButtonElement): void {
    trigger = button;
    opened = open === economy ? null : { id64, economy };
    if (open === null) button.focus({ preventScroll: true });
  }

  $effect.pre(() => {
    if (opened !== null && opened.id64 !== id64) opened = null;
  });

  /**
   * After a republish, reload the ratings and the open explanation together.
   * The explanation key is under the ratings key, so one prefix covers both,
   * even when the reloaded ratings come back with the same identity.
   */
  function reloadRatings(): void {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.systemRatings(id64),
    });
  }

  const dialogId = $derived(`${headingId}-rating-evidence`);
  const selectedRating = $derived(
    ordered.find((rating) => rating.economy === open),
  );
</script>

<section
  class="system-ratings"
  aria-labelledby={`${headingId}-ratings`}
  aria-busy={ratings.isPending}
>
  <p class="eyebrow">Ratings V4</p>
  <h2 id={`${headingId}-ratings`}>Economy potential</h2>
  {#if ratings.isPending}
    <p class="state-copy" role="status">Loading economy ratings…</p>
  {:else if ratings.isError}
    {#if isAbsent(ratings.error)}
      <p class="state-copy" data-unavailable-fields data-availability="unknown">
        <strong>{AVAILABILITY_LABELS.unknown}</strong> — the published source has
        no ratings for this system.
      </p>
    {:else}
      <div
        class="system-ratings-error"
        role="alert"
        data-unavailable-fields
        data-availability="unavailable"
      >
        <p class="state-copy">
          <strong>{AVAILABILITY_LABELS.unavailable}</strong> — economy ratings could
          not be loaded.
        </p>
        <button
          class="secondary-button"
          type="button"
          onclick={() => ratings.refetch()}
        >
          Retry
        </button>
      </div>
    {/if}
  {:else if ratings.data}
    <p class="system-ratings-note">
      Seven independent potentials, each 0–100. Confidence shows how much of a
      rating's evidence the catalogue knows; it is not a measured probability.
    </p>
    <div class="rating-table-scroll">
      <table class="rating-table" aria-labelledby={`${headingId}-ratings`}>
        <thead>
          <tr>
            <th scope="col">Economy</th>
            <th scope="col">Potential</th>
            <th scope="col">Confidence</th>
            <th scope="col">
              <span class="rating-column-label">Explanation</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {#each ordered as rating (rating.economy)}
            {@const label = ratingEconomyLabel(rating.economy)}
            <tr
              data-rating-economy={rating.economy}
              class:open={open === rating.economy}
            >
              <th scope="row">{label}</th>
              <td>
                <div class="rating-potential">
                  <div class="rating-meter" aria-hidden="true">
                    <div style:width={`${rating.potential}%`}></div>
                  </div>
                  <div>{rating.potential}</div>
                </div>
              </td>
              <td>{percent(rating.confidence)}</td>
              <td>
                <button
                  type="button"
                  class="rating-why"
                  data-inspect-popout-trigger
                  aria-haspopup="dialog"
                  aria-label={`Why? ${label}`}
                  aria-expanded={open === rating.economy}
                  aria-controls={open === rating.economy ? dialogId : undefined}
                  onclick={(event) =>
                    toggle(rating.economy, event.currentTarget)}
                >
                  Why?
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <InspectPopout
      id={dialogId}
      title={selectedRating
        ? `Why ${ratingEconomyLabel(selectedRating.economy)} scores ${selectedRating.potential}`
        : 'Economy evidence'}
      open={open !== null}
      {active}
      {trigger}
      kind="evidence"
      onclose={() => (opened = null)}
    >
      <div class="rating-explanation">
        {#if explanation.isPending}
          <p role="status">Loading the explanation…</p>
        {:else if explanation.isError}
          <p
            role="alert"
            data-unavailable-fields
            data-availability={isAbsent(explanation.error)
              ? 'unknown'
              : 'unavailable'}
          >
            {#if isAbsent(explanation.error)}
              <strong>{AVAILABILITY_LABELS.unknown}</strong> — the published source
              has no explanation for this system.
            {:else}
              <strong>{AVAILABILITY_LABELS.unavailable}</strong> — the explanation
              could not be loaded.
            {/if}
          </p>
          {#if !isAbsent(explanation.error)}
            <button
              class="quiet-button"
              type="button"
              onclick={() => explanation.refetch()}
            >
              Retry
            </button>
          {/if}
        {:else if explanation.data?.kind === 'republished'}
          <p role="alert">
            The ratings were republished while this page was open.
          </p>
          <button class="quiet-button" type="button" onclick={reloadRatings}>
            Reload ratings
          </button>
        {:else if explained && explanation.data}
          <RatingExplanation
            {explained}
            identity={explanation.data.identity}
            {bodyNames}
          />
        {/if}
      </div>
    </InspectPopout>
  {/if}
</section>

<style>
  /* Rows read as raised, rounded strips; the row whose explanation is open is
     picked out in orange so the pop-out and its row stay linked. */
  .rating-table {
    border-collapse: separate;
    border-spacing: 0 0.4rem;
  }
  .rating-table tbody :is(th, td) {
    border-bottom: 0;
    background: var(--panel-raised);
    vertical-align: middle;
  }
  .rating-table tbody :is(th, td):first-child {
    border-radius: var(--radius-pill, 0.7rem) 0 0 var(--radius-pill, 0.7rem);
  }
  .rating-table tbody :is(th, td):last-child {
    border-radius: 0 var(--radius-pill, 0.7rem) var(--radius-pill, 0.7rem) 0;
  }
  .rating-table tbody tr.open :is(th, td) {
    background: color-mix(in srgb, var(--accent) 14%, var(--panel-raised));
    box-shadow:
      inset 0 1px var(--accent),
      inset 0 -1px var(--accent);
  }
  .rating-table tbody tr.open :is(th, td):first-child {
    box-shadow:
      inset 0 1px var(--accent),
      inset 0 -1px var(--accent),
      inset 1px 0 var(--accent);
  }
  .rating-table tbody tr.open :is(th, td):last-child {
    box-shadow:
      inset 0 1px var(--accent),
      inset 0 -1px var(--accent),
      inset -1px 0 var(--accent);
  }
  .rating-meter {
    height: 0.6rem;
    background: var(--panel-sunken);
  }
  .rating-meter > div {
    background: linear-gradient(90deg, var(--neon), var(--neon-hi));
  }
  .rating-table tbody tr.open .rating-meter > div {
    background: linear-gradient(90deg, var(--accent), var(--accent-hi));
  }
  .rating-potential > div:last-child {
    font-weight: 700;
  }
  .rating-why {
    border-color: var(--line);
    border-radius: var(--radius, 0.4rem);
    background: var(--panel-raised);
    color: var(--neon);
  }
  .rating-why[aria-expanded='true'] {
    border-color: var(--accent);
  }
  .system-ratings-note {
    color: var(--text-muted);
  }
</style>
