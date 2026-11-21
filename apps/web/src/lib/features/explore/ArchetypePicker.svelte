<script lang="ts">
  import type { Snippet } from 'svelte';
  import {
    ANY_ARCHETYPE,
    ANY_ARCHETYPE_DESCRIPTION,
    ANY_ARCHETYPE_LABEL,
    ARCHETYPES,
    DEFAULT_ANY_MINIMUM_TIER,
    DEFAULT_MINIMUM_TIER,
    parseAnyMinimumTier,
    parseArchetypeKey,
    type ArchetypeKey,
    type FinderMinimumTier,
  } from './archetypes';

  // The Finder's colony type and "Show only" choices: two native dropdowns
  // side by side, as on Home, with the chosen type's description under them.
  let {
    archetype,
    minimumTier,
    anyFloor = false,
    onArchetypeChange,
    onMinimumTierChange,
    disabled = false,
    rankingTools,
  }: {
    archetype: typeof ANY_ARCHETYPE | ArchetypeKey;
    minimumTier: FinderMinimumTier | null;
    /** Offer "Show only" for Any too: only near a system, where Find lists
     *  nearest first. Galaxy-wide the list is best first already. */
    anyFloor?: boolean;
    onArchetypeChange: (archetype: typeof ANY_ARCHETYPE | ArchetypeKey) => void;
    onMinimumTierChange: (tier: FinderMinimumTier) => void;
    disabled?: boolean;
    rankingTools?: Snippet;
  } = $props();

  const id = $props.id();
  const choices = [
    {
      key: ANY_ARCHETYPE,
      label: ANY_ARCHETYPE_LABEL,
      description: ANY_ARCHETYPE_DESCRIPTION,
    },
    ...ARCHETYPES,
  ];
  const chosen = $derived(
    choices.find((choice) => choice.key === archetype) ?? choices[0],
  );
</script>

<div class="archetype-controls">
  <div class="archetype-row">
    <label class="archetype-field" for={`${id}-archetype`}>
      Colony archetype
      <select
        id={`${id}-archetype`}
        {disabled}
        value={archetype}
        aria-describedby={`${id}-archetype-description`}
        onchange={(event) => {
          const value = event.currentTarget.value;
          const key =
            value === ANY_ARCHETYPE ? ANY_ARCHETYPE : parseArchetypeKey(value);
          if (key) onArchetypeChange(key);
        }}
      >
        {#each choices as choice (choice.key)}
          <option value={choice.key}>{choice.label}</option>
        {/each}
      </select>
    </label>
    {#if archetype !== ANY_ARCHETYPE || anyFloor}
      <label class="minimum-tier" for={`${id}-minimum-tier`}>
        Show only
        <select
          id={`${id}-minimum-tier`}
          {disabled}
          value={minimumTier ??
            (archetype === ANY_ARCHETYPE
              ? DEFAULT_ANY_MINIMUM_TIER
              : DEFAULT_MINIMUM_TIER)}
          onchange={(event) => {
            const value = parseAnyMinimumTier(event.currentTarget.value);
            if (value) onMinimumTierChange(value);
          }}
        >
          {#if archetype === ANY_ARCHETYPE}
            <option value="all">All systems</option>
            <option value="C">Tier C or better</option>
          {/if}
          <option value="B">Tier B or better</option>
          <option value="A">Tier A or better</option>
          <option value="S">Tier S</option>
        </select>
      </label>
    {/if}
  </div>
  <p id={`${id}-archetype-description`} class="archetype-description">
    {chosen.description}
  </p>
  <div class="ranking-tools">{@render rankingTools?.()}</div>
</div>

<style>
  .archetype-controls {
    display: grid;
    gap: 0.5rem;
    min-width: 0;
  }
  .archetype-row {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 0.6rem 1rem;
  }
  .archetype-field,
  .minimum-tier {
    display: grid;
    gap: 0.3rem;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .archetype-field {
    flex: 1 1 13rem;
  }
  .archetype-field select {
    width: 100%;
  }
  select {
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
  .archetype-description {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.8rem;
    line-height: 1.45;
  }
  .ranking-tools {
    min-width: 0;
  }
  .ranking-tools:empty {
    display: none;
  }
</style>
