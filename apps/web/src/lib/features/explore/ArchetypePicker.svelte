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
    type ArchetypeKey,
    type FinderMinimumTier,
  } from './archetypes';

  let {
    archetype,
    minimumTier,
    anyFloor = false,
    onArchetypeChange,
    onMinimumTierChange,
    disabled = false,
    compact = false,
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
    /** Narrow panel: names only on screen; descriptions stay for assistive tech. */
    compact?: boolean;
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
</script>

<div class="archetype-controls" class:is-compact={compact}>
  <fieldset {disabled}>
    <legend>Colony archetype</legend>
    <div class="archetype-choices">
      {#each choices as choice (choice.key)}
        <div class="archetype-choice">
          <input
            id={`${id}-${choice.key}`}
            name={`${id}-archetype`}
            type="radio"
            value={choice.key}
            checked={archetype === choice.key}
            aria-describedby={`${id}-${choice.key}-description`}
            onchange={() => onArchetypeChange(choice.key)}
          />
          <label for={`${id}-${choice.key}`}>{choice.label}</label>
          <p id={`${id}-${choice.key}-description`}>{choice.description}</p>
        </div>
      {/each}
    </div>
  </fieldset>
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
  <div class="ranking-tools">{@render rankingTools?.()}</div>
</div>

<style>
  .archetype-controls {
    display: grid;
    gap: 0.85rem;
    min-width: 0;
    margin-block: 1.5rem;
  }
  fieldset {
    min-width: 0;
    margin: 0;
    padding: 1rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    background: var(--panel-gloss), var(--panel);
    box-shadow: var(--shadow-panel);
  }
  legend {
    padding-inline: 0.4rem;
    font-family: var(--font-hud);
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .archetype-choices {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
    gap: 0.75rem;
  }
  .archetype-choice {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-content: start;
    gap: 0.4rem 0.65rem;
    padding: 0.85rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
  }
  .archetype-choice:has(input:not(:disabled)):hover {
    border-color: var(--line-strong);
  }
  .archetype-choice:has(input:checked) {
    border-color: var(--accent);
    background: var(--neon-soft);
  }
  .archetype-choice:has(input:focus-visible) {
    outline: 2px solid var(--focus);
    outline-offset: 3px;
  }
  input {
    width: 1.1rem;
    height: 1.1rem;
    margin: 0.15rem 0 0;
    accent-color: var(--accent);
  }
  .archetype-choice label {
    font-weight: 600;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }
  label,
  input {
    cursor: pointer;
  }
  .archetype-choice p {
    grid-column: 2;
    margin: 0;
    color: var(--text-muted);
    font-size: 0.8rem;
    line-height: 1.45;
  }
  .minimum-tier {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
  select {
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    color: var(--text);
    font: inherit;
  }
  .ranking-tools {
    min-width: 0;
  }
  .ranking-tools:empty {
    display: none;
  }
  .is-compact {
    gap: 0.5rem;
    margin-block: 0;
  }
  .is-compact fieldset {
    padding: 0.5rem 0.6rem 0.6rem;
  }
  .is-compact legend {
    font-size: 0.85rem;
  }
  .is-compact .archetype-choices {
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 9rem), 1fr));
    gap: 0.3rem;
  }
  .is-compact .archetype-choice {
    align-items: center;
    gap: 0.4rem;
    padding: 0.35rem 0.5rem;
  }
  .is-compact .archetype-choice label {
    font-size: 0.78rem;
    line-height: 1.2;
    overflow-wrap: normal;
  }
  .is-compact input {
    margin: 0;
  }
  .is-compact .archetype-choice p {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
