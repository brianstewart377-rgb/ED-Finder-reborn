<script lang="ts">
  import { untrack } from 'svelte';
  import {
    getArchetypeExplanation,
    type ArchetypeExplanationEntry,
    type ArchetypeExplanationIdentity,
    type ArchetypeExplanationResult,
    type ExploreSystem,
  } from '$lib/api/client';
  import { queryClient, queryKeys } from '$lib/api/query';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import {
    ANY_ARCHETYPE,
    archetypeLabel,
    isArchetypeKey,
    tierLabel,
    type ArchetypeKey,
  } from './archetypes';
  import { colonyTypeReason } from './colony-type-reasons';

  // Why the selected result scored what it did, in plain words: the colony
  // type the list is ranked by (or, for Any, the one the system suits best)
  // straight away, and every other colony type on request. One small request,
  // made when the result is selected; only the selected result has this.
  let { system, expected, archetype } = $props<{
    system: ExploreSystem;
    expected: ArchetypeExplanationIdentity | null;
    archetype: typeof ANY_ARCHETYPE | ArchetypeKey;
  }>();

  const id = $props.id();
  const othersId = `${id}-others`;
  const identityKey = $derived(
    JSON.stringify(queryKeys.archetypeExplanation(system.id64, expected)),
  );

  let requested = $state<{
    key: string;
    data: ArchetypeExplanationResult | null;
  } | null>(null);
  const explanation = $derived(
    requested?.key === identityKey ? requested.data : null,
  );
  let requestSequence = 0;

  async function loadExplanation(retry = false): Promise<void> {
    if (!FINDER_F4_ENABLED || !expected) return;
    const key = identityKey;
    const id64 = system.id64;
    const identity = expected;
    const sequence = ++requestSequence;
    requested = { key, data: null };
    let data: ArchetypeExplanationResult;
    try {
      data = await queryClient.fetchQuery({
        queryKey: queryKeys.archetypeExplanation(id64, identity),
        queryFn: ({ signal }) =>
          getArchetypeExplanation(id64, identity, signal),
        retry: false,
        ...(retry ? { staleTime: 0 } : {}),
      });
    } catch {
      data = { kind: 'error', status: 0 };
    }
    // A late answer for a previous result must never replace its successor.
    if (identityKey === key && requestSequence === sequence) {
      requested = { key, data };
    }
  }

  // Asked for again whenever the result, or the scores behind it, change.
  $effect(() => {
    void identityKey;
    untrack(() => void loadExplanation());
  });

  // The other colony types stay folded until asked for, for each result.
  let othersOpenFor = $state<string | null>(null);
  const othersOpen = $derived(othersOpenFor === identityKey);

  const entries = $derived(
    explanation?.kind === 'explained' ? explanation.archetypes : [],
  );
  // The colony type the list is ranked by; for Any, the one the system suits
  // best, whose score is its Best Colony Potential.
  const lead = $derived.by((): ArchetypeExplanationEntry | null => {
    if (explanation?.kind !== 'explained') return null;
    const wanted =
      archetype !== ANY_ARCHETYPE
        ? archetype
        : explanation.summary.primary_archetype;
    return (
      (isArchetypeKey(wanted)
        ? entries.find((entry) => entry.key === wanted)
        : undefined) ??
      [...entries].sort((a, b) => b.score - a.score)[0] ??
      null
    );
  });
  const others = $derived(
    entries
      .filter((entry) => entry.key !== lead?.key)
      .sort((a, b) => b.score - a.score),
  );
</script>

{#snippet reasons(entry: ArchetypeExplanationEntry)}
  {@const reason = colonyTypeReason(entry)}
  <p class="reason-needs">{reason.needs}</p>
  {#if reason.economies.length}
    <ul class="reason-economies">
      {#each reason.economies as line, index (index)}
        <li>{line}</li>
      {/each}
    </ul>
  {/if}
  {#if reason.steps.length}
    <p class="reason-sum">How that makes {entry.score}:</p>
    <ul class="reason-steps">
      {#each reason.steps as step, index (index)}
        <li>{step}</li>
      {/each}
    </ul>
  {/if}
{/snippet}

{#if FINDER_F4_ENABLED}
  <section
    class="archetype-explanation"
    data-archetype-explanation
    aria-label={`Why ${system.name ?? 'this system'} got its score`}
  >
    {#if !expected || explanation?.kind === 'explanation_unavailable'}
      <p role="status">
        The reasons for this score can't be shown: the scores have been updated
        since this list was made. Search again to see them.
      </p>
    {:else if explanation === null}
      <p role="status">Loading why it scored this…</p>
    {:else if explanation.kind === 'error'}
      <p role="alert">
        {explanation.status === 404
          ? 'This system has no colony type explanation yet.'
          : 'Colony type explanations are temporarily unavailable.'}
      </p>
      <button
        type="button"
        class="quiet-button"
        onclick={() => loadExplanation(true)}>Try again</button
      >
    {:else if lead}
      <div data-explanation-archetype={lead.key} data-explanation-lead>
        <h3 id={`${id}-lead`}>
          Why {archetypeLabel(lead.key)} scores {lead.score}
        </h3>
        {@render reasons(lead)}
      </div>
      <button
        type="button"
        class="quiet-button explanation-toggle"
        data-archetype-explanation-toggle
        aria-expanded={othersOpen}
        aria-controls={othersId}
        onclick={() => (othersOpenFor = othersOpen ? null : identityKey)}
      >
        Every other colony type
      </button>
      <div id={othersId} hidden={!othersOpen}>
        {#if othersOpen}
          {#each others as entry (entry.key)}
            <section
              class="other-type"
              data-explanation-archetype={entry.key}
              aria-labelledby={`${id}-${entry.key}`}
            >
              <h3 id={`${id}-${entry.key}`}>
                {archetypeLabel(entry.key)}
                <span class="explanation-score">{entry.score}</span>
                <span class="tier-badge kit-chip" data-tier={entry.tier}
                  >{tierLabel(entry.tier)}</span
                >
              </h3>
              {@render reasons(entry)}
            </section>
          {/each}
        {/if}
      </div>
    {/if}
  </section>
{/if}

<style>
  .archetype-explanation {
    min-width: 0;
    padding: 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    font-size: 0.85rem;
    line-height: 1.5;
  }
  h3 {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin: 0 0 0.35rem;
    font-size: 0.95rem;
  }
  p {
    margin: 0.35rem 0;
  }
  .reason-sum {
    margin-top: 0.6rem;
    color: var(--text-muted);
  }
  ul {
    margin: 0.25rem 0 0;
    padding: 0;
    list-style: none;
  }
  /* Result rows style every `li` in the list: these are plain lines. */
  li {
    display: block;
    margin: 0.15rem 0;
    padding: 0;
    border: 0;
    background: none;
  }
  .reason-economies li {
    padding-left: 0.9rem;
    text-indent: -0.9rem;
  }
  .reason-economies li::before {
    content: '•';
    display: inline-block;
    width: 0.9rem;
    text-indent: 0;
    color: var(--text-muted);
  }
  .reason-steps {
    font-variant-numeric: tabular-nums;
  }
  .explanation-toggle {
    margin-top: 0.75rem;
    padding: 0.25rem 0.7rem;
    font: inherit;
  }
  .other-type {
    margin-top: 0.75rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--line);
  }
  .explanation-score {
    font-variant-numeric: tabular-nums;
  }
  .tier-badge {
    padding: 0.05rem 0.4rem;
    border: 1px solid currentColor;
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    font-size: 0.75rem;
    font-weight: 400;
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
</style>
