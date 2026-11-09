<script lang="ts">
  import type {
    PlanBodiesV1,
    PlanEvaluationV1,
    PlanFacilityV1,
  } from '$lib/api';
  import type { PlannerModel } from './model';
  import EconomyBar from './EconomyBar.svelte';
  import {
    ECONOMY_CHANGE_THRESHOLD,
    economyPercent,
    economyTotal,
    type EconomyWhySelection,
  } from './economy-presentation';

  type Props = {
    model: PlannerModel;
    facilities: PlanFacilityV1[];
    bodies: PlanBodiesV1;
    evaluation: PlanEvaluationV1 | null;
    stale?: boolean;
    onwhy: (selection: EconomyWhySelection) => void;
  };
  let {
    model,
    facilities,
    bodies,
    evaluation,
    stale = false,
    onwhy,
  }: Props = $props();
  const id = $props.id();
  const portIds = $derived(
    new Set(
      facilities
        .filter((facility) => facility.is_port)
        .map((facility) => facility.id),
    ),
  );
  const ports = $derived(
    model.sites.filter((site) => portIds.has(site.facility_id)),
  );
  const siteNames = $derived(
    Object.fromEntries(
      model.sites.map((site) => [
        site.site_id,
        site.name ||
          facilities.find((facility) => facility.id === site.facility_id)
            ?.name ||
          site.site_id,
      ]),
    ),
  );
  const currentEvaluation = $derived(
    evaluation?.system_id64 === model.system_id64 ? evaluation : null,
  );
</script>

<section
  class="plan-pane economy-bars"
  data-plan-economies
  aria-labelledby={`${id}-title`}
>
  <h2 id={`${id}-title`}>Port economies</h2>
  {#if stale && currentEvaluation}
    <p class="muted" role="status">
      Last good evaluation — awaiting a valid current result. Economy bars may
      not match the current plan.
    </p>
  {/if}
  {#if currentEvaluation && currentEvaluation.economies.notes.length > 0}
    <ul class="economy-notes" aria-label="Economy notes">
      {#each currentEvaluation.economies.notes as note, index (index)}
        <li>
          {#if note.site_id}<strong
              >{`${siteNames[note.site_id] || note.site_id}: `}</strong
            >{/if}{note.message}
        </li>
      {/each}
    </ul>
  {/if}
  {#if bodies.economy_facts_generation_id == null}
    <p class="muted">
      Economy facts unavailable for this system's published generation
    </p>
  {:else if !currentEvaluation}
    <p class="muted">
      Economy bars are waiting for a successful plan evaluation.
    </p>
  {:else if ports.length === 0}
    <p class="muted">Add a port to see its economies.</p>
  {:else}
    <p class="muted">
      Each economy uses the returned strength (1 = 100%). Underlined changes
      mark economies that appear, disappear or change by more than {ECONOMY_CHANGE_THRESHOLD}
      percentage points. Focus or hover a segment for its breakdown; activate it to
      open Why.
    </p>
    <div class="economy-ports">
      {#each ports as site (site.site_id)}
        {@const now =
          currentEvaluation.economies.now.find(
            (port) => port.site_id === site.site_id,
          )?.economies ?? []}
        {@const after =
          currentEvaluation.economies.after_plan.find(
            (port) => port.site_id === site.site_id,
          )?.economies ?? []}
        {@const scale = Math.max(1, economyTotal(now), economyTotal(after))}
        <article
          class="economy-port"
          data-economy-port={site.site_id}
          aria-labelledby={`${id}-${site.site_id}`}
        >
          <h3 id={`${id}-${site.site_id}`}>
            {siteNames[site.site_id]}
            {#if model.sites[0]?.site_id === site.site_id}<span class="primary"
                >Primary Port</span
              >{/if}
          </h3>
          <p class="muted">Shared bar scale: 0–{economyPercent(scale)}.</p>
          <div class="economy-views">
            <EconomyBar
              siteId={site.site_id}
              view="now"
              {now}
              {after}
              {scale}
              {siteNames}
              {onwhy}
            />
            <EconomyBar
              siteId={site.site_id}
              view="after_plan"
              {now}
              {after}
              {scale}
              {siteNames}
              {onwhy}
            />
          </div>
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .economy-bars {
    margin-top: 1rem;
  }
  h2 {
    margin: 0;
    font-size: 1.1rem;
  }
  h3 {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin: 0;
    font-size: 0.95rem;
  }
  .muted {
    color: var(--text-muted, var(--muted));
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .primary {
    border: 1px solid var(--line);
    border-radius: 0.3rem;
    padding: 0.15rem 0.35rem;
    font-size: 0.72rem;
    font-weight: normal;
  }
  .economy-ports {
    display: grid;
    gap: 1rem;
  }
  .economy-port {
    min-width: 0;
    padding-top: 1rem;
    border-top: 1px solid var(--line);
  }
  .economy-views {
    display: grid;
    gap: 1rem;
  }
  .economy-notes {
    padding-left: 1.25rem;
    font-size: 0.85rem;
    line-height: 1.6;
  }
</style>
