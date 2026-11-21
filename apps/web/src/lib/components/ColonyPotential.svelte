<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { ApiError, getSystemArchetypes } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import {
    ARCHETYPES,
    archetypeLabel,
    evidenceBadge,
    isTier,
    tierLabel,
  } from '$lib/features/explore/archetypes';

  // What Find showed for this system: its best colony potential with the
  // API's tier, the colony types it suits best, how sure the scores are, and
  // every colony type's own score. One small request, made when the Overview
  // is shown.
  let {
    id64,
    headingId,
    active = true,
  }: { id64: Id64; headingId: string; active?: boolean } = $props();

  // 404: not scored (Find scores systems within 500 ly of inhabited space).
  // 422: an id64 beyond the signed 64-bit key space.
  const isAbsent = (error: unknown): boolean =>
    error instanceof ApiError && (error.status === 404 || error.status === 422);

  const scores = createQuery(() => ({
    queryKey: queryKeys.systemArchetypes(id64),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      getSystemArchetypes(id64, signal),
    enabled: active,
    retry: (failureCount: number, error: unknown) =>
      !isAbsent(error) && failureCount < 1,
  }));

  const known = (value: unknown): value is number =>
    typeof value === 'number' && Number.isFinite(value);

  const data = $derived(scores.data);
  const overall = $derived(
    known(data?.overall_development_potential)
      ? Math.round(data.overall_development_potential)
      : null,
  );
  const tier = $derived(isTier(data?.best_tier) ? data.best_tier : null);
  const primary = $derived(archetypeLabel(data?.primary_archetype));
  const secondary = $derived(archetypeLabel(data?.secondary_archetype));
  const evidence = $derived(
    evidenceBadge('any', data?.confidence, data?.completeness),
  );
  // Every colony type, best first; the API's own tier with each.
  const types = $derived.by(() => {
    const archetypes = (data?.archetypes ?? {}) as Record<
      string,
      { score?: unknown; tier?: unknown } | undefined
    >;
    return ARCHETYPES.map(({ key, label }) => {
      const entry = archetypes[key];
      return {
        key,
        label,
        score: known(entry?.score) ? Math.round(entry.score) : null,
        tier: isTier(entry?.tier) ? entry.tier : null,
      };
    }).sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  });
</script>

<section
  class="colony-potential"
  aria-labelledby={`${headingId}-colony`}
  aria-busy={scores.isPending && active}
>
  <h2 id={`${headingId}-colony`}>Colony potential</h2>
  {#if scores.isPending}
    <p class="state-copy" role="status">Reading colony potential…</p>
  {:else if scores.isError}
    {#if isAbsent(scores.error)}
      <p class="state-copy">
        Not scored. Find scores systems within 500 LY of inhabited space.
      </p>
    {:else}
      <div class="colony-potential-error" role="alert">
        <p class="state-copy">Colony potential could not be loaded.</p>
        <button
          class="secondary-button"
          type="button"
          onclick={() => scores.refetch()}>Retry</button
        >
      </div>
    {/if}
  {:else}
    <div class="headline">
      <span class="score">{overall ?? '–'}</span>
      <div class="headline-text">
        {#if tier}<span class="tier-badge kit-chip" data-tier={tier}
            >{tierLabel(tier)}</span
          >{/if}
        <span class="muted">Best colony potential, out of 100</span>
      </div>
    </div>
    <p class="suits">
      <span
        >{#if primary}Suits <strong>{primary}</strong>{#if secondary}, then <strong
              >{secondary}</strong
            >{/if}.{:else}No colony type stands out.{/if}</span
      >
      <span class="evidence" title={evidence.description}>
        {evidence.label}</span
      >
    </p>
    <ol class="type-scores" aria-label="Every colony type, best first">
      {#each types as type (type.key)}
        <li>
          <span class="type-name">{type.label}</span>
          <span class="type-score">{type.score ?? '–'}</span>
          {#if type.tier}<span class="tier-badge kit-chip" data-tier={type.tier}
              >{tierLabel(type.tier)}</span
            >{/if}
        </li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  .colony-potential {
    margin-bottom: 1.5rem;
  }
  .colony-potential h2 {
    margin: 0.2rem 0 0.75rem;
    font-size: clamp(1.15rem, 2vw, 1.55rem);
  }
  .headline {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .score {
    font-family: var(--font-hud);
    font-size: 2.6rem;
    font-weight: 700;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .headline-text {
    display: grid;
    gap: 0.3rem;
    justify-items: start;
  }
  .muted,
  .evidence {
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .suits {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.25rem 0.75rem;
    margin: 0.85rem 0;
  }
  .type-scores {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr));
    gap: 0.35rem 1.5rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .type-scores li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 2.2rem auto;
    align-items: center;
    gap: 0.5rem;
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.88rem;
  }
  .type-name {
    color: var(--text-muted);
  }
  .type-score {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .tier-badge {
    width: fit-content;
    padding: 0.05rem 0.4rem;
    border: 1px solid currentColor;
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    font-size: 0.75rem;
    white-space: nowrap;
  }
  [data-tier='S'] {
    color: var(--color-tier-s);
  }
  [data-tier='A'] {
    color: var(--color-tier-a);
  }
  [data-tier='B'] {
    color: var(--color-tier-b);
  }
  [data-tier='C'] {
    color: var(--color-tier-c);
  }
  [data-tier='D'] {
    color: var(--color-tier-d);
  }
  .colony-potential-error {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
</style>
