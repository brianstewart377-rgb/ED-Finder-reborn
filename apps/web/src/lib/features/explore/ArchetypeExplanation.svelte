<script lang="ts">
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
    evidenceBadge,
    tierLabel,
    type ArchetypeKey,
  } from './archetypes';

  let { system, expected, archetype, headingId } = $props<{
    system: ExploreSystem;
    expected: ArchetypeExplanationIdentity | null;
    archetype: typeof ANY_ARCHETYPE | ArchetypeKey;
    headingId: string;
  }>();

  const id = $props.id();
  const regionId = `${id}-explanation`;
  const identityKey = $derived(
    JSON.stringify(queryKeys.archetypeExplanation(system.id64, expected)),
  );
  let openedFor = $state<string | null>(null);
  const expanded = $derived(openedFor === identityKey);
  const mode = $derived(archetype === ANY_ARCHETYPE ? 'any' : 'selected');

  // Reused cards must be expanded again after their system or product changes.
  $effect(() => {
    if (openedFor !== identityKey) openedFor = null;
  });

  let requested = $state<{
    key: string;
    data: ArchetypeExplanationResult | null;
  } | null>(null);
  const explanation = $derived(
    requested?.key === identityKey ? requested.data : null,
  );
  let requestSequence = 0;

  // Even query-cache entries are lazy: collapsed result cards add no queries
  // to the Finder's bounded workload. Every actual request uses the shared key.
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
    // A late response for a previous card must never replace its successor.
    if (identityKey === key && requestSequence === sequence) {
      requested = { key, data };
    }
  }

  function toggle(): void {
    if (expanded) openedFor = null;
    else {
      openedFor = identityKey;
      void loadExplanation();
    }
  }

  const economies: Readonly<Record<number, string>> = {
    1: 'Agriculture',
    2: 'Refinery',
    3: 'Industrial',
    4: 'High Tech',
    5: 'Military',
    6: 'Tourism',
    7: 'Extraction',
  };
  const record = (value: unknown): Record<string, unknown> =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const numeric = (value: unknown): value is number =>
    typeof value === 'number' && Number.isFinite(value);
  const number = (value: unknown): string =>
    numeric(value)
      ? value.toLocaleString('en-US', { maximumFractionDigits: 4 })
      : 'unknown';
  const economyOrdinals = (value: unknown): number[] =>
    Array.isArray(value)
      ? value.filter(
          (ordinal): ordinal is number =>
            numeric(ordinal) && Object.hasOwn(economies, ordinal),
        )
      : [];

  /** Translate the endpoint's open explanation object; never recompute scores. */
  function contributions(entry: ArchetypeExplanationEntry): string[] {
    const details = entry.explanation;
    if (entry.key === 'flexible') {
      return [
        `${number(details.breadth)} qualifying economies toward the target of ${number(details.breadth_target)}.`,
      ];
    }
    const anchors = economyOrdinals(details.anchors);
    const supporting = economyOrdinals(details.supporting);
    const components = record(details.components);
    const potential = record(components.pot);
    const quality = record(components.qual);
    const opportunities = record(components.opp_count);
    return [
      `Required economies: ${anchors.map((ordinal) => economies[ordinal]).join(', ') || 'unknown'}.`,
      ...(supporting.length
        ? [
            `Supporting economies: ${supporting.map((ordinal) => economies[ordinal]).join(', ')}.`,
          ]
        : []),
      ...anchors.map(
        (ordinal) =>
          `${economies[ordinal]}: potential ${number(potential[ordinal])} out of 100; quality ${
            quality[ordinal] === null
              ? 'unknown (bounded estimate used)'
              : numeric(quality[ordinal])
                ? `${number(quality[ordinal])} out of 100`
                : 'unknown'
          }; ${number(opportunities[ordinal])} opportunities.`,
      ),
      ...supporting.map(
        (ordinal) =>
          `${economies[ordinal]}: potential ${number(potential[ordinal])} out of 100.`,
      ),
      `Economy strength: ${number(details.core)} points before adjustments.`,
      `Specialisation adjustment: ×${number(details.spec)}.`,
      `Opportunity capacity adjustment: ×${number(details.cap)}.`,
      `Economy synergy: ${number(details.synergy)} additional points.`,
    ];
  }
</script>

{#if FINDER_F4_ENABLED}
  <div class="archetype-explanation">
    <button
      type="button"
      class="quiet-button explanation-toggle"
      data-archetype-explanation-toggle
      aria-expanded={expanded}
      aria-controls={regionId}
      onclick={toggle}
    >
      Explain archetypes
    </button>
    <div
      id={regionId}
      role="region"
      aria-labelledby={headingId}
      data-archetype-explanation
      hidden={!expanded}
    >
      {#if expanded}
        {#if !expected || explanation?.kind === 'explanation_unavailable'}
          <p role="status">Explanation unavailable for this ranking version</p>
        {:else if explanation === null}
          <p role="status">Loading archetype explanations…</p>
        {:else if explanation.kind === 'error'}
          <p role="alert">
            {explanation.status === 404
              ? 'This system has no published archetype explanation.'
              : 'Archetype explanations are temporarily unavailable.'}
          </p>
          <button
            type="button"
            class="quiet-button"
            onclick={() => loadExplanation(true)}>Try again</button
          >
        {:else if explanation.kind === 'explained'}
          {#each explanation.archetypes as entry (entry.key)}
            {@const badge = evidenceBadge(
              mode,
              entry.confidence,
              explanation.completeness,
            )}
            <section
              class="archetype-contributions"
              data-explanation-archetype={entry.key}
              aria-labelledby={`${id}-${entry.key}-heading`}
            >
              <h3 id={`${id}-${entry.key}-heading`}>
                {archetypeLabel(entry.key)}
              </h3>
              <p>
                Score: <span class="explanation-score">{entry.score}</span> ·
                <span class="tier-badge" data-tier={entry.tier}
                  >{tierLabel(entry.tier)}</span
                >
              </p>
              <p
                class="evidence-badge"
                aria-describedby={`${id}-${entry.key}-evidence`}
              >
                {badge.label}
              </p>
              <p
                id={`${id}-${entry.key}-evidence`}
                class="evidence-description"
              >
                {badge.description}
              </p>
              <ul class="contribution-list">
                {#each contributions(entry) as contribution, index (index)}
                  <li class="contribution">{contribution}</li>
                {/each}
              </ul>
            </section>
          {/each}
        {/if}
      {/if}
    </div>
  </div>
{/if}

<style>
  .archetype-explanation {
    grid-column: 1 / -1;
    min-width: 0;
    padding: 0 0.8rem 0.8rem 1.15rem;
    font-size: 0.85rem;
  }
  .explanation-toggle {
    margin-bottom: 0.5rem;
  }
  .archetype-contributions {
    padding-block: 0.75rem;
    border-top: 1px solid var(--muted);
  }
  h3 {
    margin: 0 0 0.5rem;
    font-size: 1rem;
  }
  p {
    margin-block: 0.5rem;
  }
  .tier-badge {
    white-space: nowrap;
    border: 1px solid currentColor;
    border-radius: 0.3rem;
    padding: 0.15rem 0.4rem;
    font-size: 0.8rem;
  }
  .evidence-badge,
  .evidence-description {
    color: var(--muted);
  }
  .contribution-list {
    margin: 0.5rem 0 0;
    padding-left: 1.25rem;
    list-style: disc;
  }
  .contribution {
    display: list-item;
    margin-block: 0.35rem;
    padding: 0;
    border: 0;
  }
</style>
