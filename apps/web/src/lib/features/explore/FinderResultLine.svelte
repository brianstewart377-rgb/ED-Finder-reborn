<script lang="ts">
  import type { ExploreSystem } from '$lib/api/client';
  import {
    ANY_ARCHETYPE,
    archetypeLabel,
    isTier,
    tierLabel,
    type ArchetypeKey,
    type Tier,
  } from './archetypes';

  // One result on one line: the score the list is ranked by with its tier,
  // the colony types the system suits, and what its bodies hold. Everything
  // else about the result is in its details, under the line once it is
  // selected (FinderResultRanking). Tiers are the API's own, never worked out
  // here from a score.
  let {
    system,
    archetype,
  }: {
    system: ExploreSystem & { best_tier?: Tier | null };
    archetype: typeof ANY_ARCHETYPE | ArchetypeKey;
  } = $props();

  const known = (value: number | null | undefined): value is number =>
    typeof value === 'number' && Number.isFinite(value);

  const selected = $derived(archetype !== ANY_ARCHETYPE);
  const score = $derived(
    selected
      ? system.score_kind === 'selected_fit' && known(system.ranking_score)
        ? system.ranking_score
        : null
      : known(system.overall_development_potential)
        ? system.overall_development_potential
        : null,
  );
  const tier = $derived.by<Tier | null>(() => {
    if (selected)
      return system.score_kind === 'selected_fit' && isTier(system.ranking_tier)
        ? system.ranking_tier
        : null;
    if (isTier(system.best_tier)) return system.best_tier;
    return system.score_kind === 'overall_potential' &&
      isTier(system.ranking_tier)
      ? system.ranking_tier
      : null;
  });
  const scoreName = $derived(
    selected ? `${archetypeLabel(archetype)} fit` : 'Best Colony Potential',
  );
  const primary = $derived(archetypeLabel(system.primary_archetype));
  const secondary = $derived(archetypeLabel(system.secondary_archetype));

  // Only what the system has: a count of nothing is left out.
  const bodies = $derived.by(() => {
    const counts: Array<[number | null | undefined, string, string]> = [
      [system.landable_count, 'landable', 'landable'],
      [system.elw_count, 'Earth-like', 'Earth-like'],
      [system.ww_count, 'water world', 'water worlds'],
      [system.ammonia_count, 'ammonia world', 'ammonia worlds'],
      [system.terraformable_count, 'terraformable', 'terraformable'],
      [system.bio_signal_total, 'bio signal', 'bio signals'],
      [system.geo_signal_total, 'geo signal', 'geo signals'],
    ];
    if (counts.every(([count]) => !known(count))) return null;
    return counts
      .filter(([count]) => known(count) && count > 0)
      .map(([count, one, many]) => `${count} ${count === 1 ? one : many}`);
  });
</script>

<span class="line-score" data-score-kind={selected ? 'fit' : 'overall'}
  ><span class="sr-only">{scoreName}:</span>
  <span class="score-value"
    >{#if score === null}<span aria-hidden="true">–</span><span class="sr-only"
        >Unknown</span
      >{:else}{score}{/if}</span
  >
  {#if tier}
    <span class="tier-badge kit-chip" data-tier={tier}>{tierLabel(tier)}</span>
  {/if}
</span>
<span class="line-types"
  ><span class="sr-only">Primary:</span>
  {primary ?? 'Unknown'}{#if secondary}<span class="sep" aria-hidden="true"
      >·</span
    ><span class="sr-only">; Secondary:</span>
    {secondary}{/if}</span
>
<span class="line-bodies">
  {#if bodies === null}
    <span class="unknown">Bodies not known</span>
  {:else if bodies.length === 0}
    <span class="unknown">No notable bodies</span>
  {:else}
    {bodies.join(' · ')}
  {/if}
</span>

<style>
  .line-score {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    white-space: nowrap;
  }
  .score-value {
    min-width: 1.8rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .tier-badge {
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
  .line-types,
  .line-bodies {
    min-width: 0;
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.35;
  }
  .unknown {
    font-style: italic;
  }
  .sep {
    margin-inline-start: 0.35em;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
