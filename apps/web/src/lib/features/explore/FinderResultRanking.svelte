<script lang="ts">
  import type { ExploreSystem, FinderPageEnvelope } from '$lib/api/client';
  import {
    ANY_ARCHETYPE,
    archetypeLabel,
    evidenceBadge,
    isTier,
    selectedFitLabel,
    tierLabel,
    type ArchetypeKey,
    type Tier,
  } from './archetypes';

  type Archetype = typeof ANY_ARCHETYPE | ArchetypeKey;
  type Props =
    | {
        variant: 'row';
        system: ExploreSystem & { best_tier?: Tier | null };
        archetype: Archetype;
        page?: never;
      }
    | {
        variant: 'summary';
        page: FinderPageEnvelope;
        archetype: Archetype;
        system?: never;
      };

  let { variant, system, page: incomingPage, archetype }: Props = $props();
  const id = $props.id();
  const descriptionId = `${id}-evidence`;
  const page = $derived.by(() => {
    if (
      incomingPage?.total === 0 &&
      incomingPage.truncation.source === 'rankings' &&
      incomingPage.truncation.is_truncated
    )
      throw new TypeError('A truncated ranking cannot have no qualifying rows');
    return incomingPage;
  });
  const mode = $derived(archetype === ANY_ARCHETYPE ? 'any' : 'selected');
  const badge = $derived(
    evidenceBadge(mode, system?.confidence, system?.completeness),
  );
  const fitLabel = $derived(
    mode === 'selected' && system?.score_kind === 'selected_fit'
      ? selectedFitLabel(
          system.ranking_score,
          system.rival_archetype,
          system.rival_score,
        )
      : null,
  );
  const number = (value: number) => value.toLocaleString('en-US');
  const known = (value: number | null | undefined): value is number =>
    typeof value === 'number' && Number.isFinite(value);
</script>

{#if variant === 'summary' && page}
  <span class="ranking-summary">
    <span>
      Showing {number(page.count)} of {number(page.total)} (results {number(
        page.count === 0 ? 0 : page.offset + 1,
      )}–{number(page.count === 0 ? 0 : page.offset + page.count)})
    </span>
    {#if page.truncation.source === 'rankings' && page.truncation.is_truncated}
      <span>within the top 10,000 for {archetypeLabel(archetype)}</span>
    {:else if page.truncation.source === 'local-search'}
      <span>
        {#if page.truncation.total_is_capped}
          10,000+ matches; navigation is limited to the first 10,000 results.
        {:else}
          {number(page.total)} {page.total === 1 ? 'match' : 'matches'}
        {/if}
      </span>
    {/if}
  </span>
{:else if variant === 'row' && system}
  <span class="ranking-facts">
    <span class="overall-potential">
      Best Colony Potential: {known(system.overall_development_potential)
        ? system.overall_development_potential
        : 'Unknown'}
      {#if isTier(system.best_tier)}
        · <span class="tier-badge kit-chip" data-tier={system.best_tier}
          >{tierLabel(system.best_tier)}</span
        >
      {/if}
    </span>
    {#if mode === 'selected'}
      <span class="selected-fit">
        {archetypeLabel(archetype)} fit: {system.score_kind ===
          'selected_fit' && known(system.ranking_score)
          ? system.ranking_score
          : 'Unknown'}
        {#if system.score_kind === 'selected_fit' && isTier(system.ranking_tier)}
          · <span class="tier-badge kit-chip" data-tier={system.ranking_tier}
            >{tierLabel(system.ranking_tier)}</span
          >
        {/if}
      </span>
    {/if}
    <span class="classification">
      <span class="classification-types">
        Primary: {archetypeLabel(system.primary_archetype) ?? 'Unknown'};
        Secondary: {archetypeLabel(system.secondary_archetype) ??
          'No clear secondary'}
      </span>
      {#if fitLabel}<span class="fit-label kit-chip">{fitLabel}</span>{/if}
    </span>
    <span class="evidence-badge kit-chip" aria-describedby={descriptionId}
      >{badge.label}</span
    >
    <span id={descriptionId} class="evidence-description"
      >{badge.description}</span
    >
  </span>
{/if}

<style>
  .ranking-facts,
  .ranking-summary {
    display: grid;
    min-width: 0;
    gap: 0.5rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .ranking-facts {
    margin-block: 0.75rem;
    padding: 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
  }
  .overall-potential,
  .selected-fit {
    font-weight: 650;
    font-variant-numeric: tabular-nums;
  }
  .selected-fit {
    color: var(--accent-hi);
  }
  .classification {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem 0.75rem;
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  .tier-badge,
  .fit-label,
  .evidence-badge {
    width: fit-content;
    max-width: 100%;
    padding: 0.15rem 0.4rem;
    border: 1px solid currentColor;
    border-radius: var(--radius-pill);
    font-size: 0.8rem;
  }
  .tier-badge {
    white-space: nowrap;
    background: var(--panel-raised);
  }
  .fit-label {
    color: var(--neon-hi);
    background: var(--neon-soft);
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
  .evidence-badge,
  .ranking-summary {
    color: var(--text-muted);
    font-size: 0.8rem;
  }
  .evidence-description {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
