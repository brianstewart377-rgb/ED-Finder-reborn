<script lang="ts">
  import type {
    PlanBodiesV1,
    PlanEvaluationV1,
    PlanFacilityV1,
  } from '$lib/api';
  import { locationSlots, type PlannerModel } from './model';
  import type { PlannerLocation } from './locations';
  import PlanIcon from './PlanIcon.svelte';
  let {
    model,
    bodies,
    facilities,
    locations,
    evaluation,
    view,
    error,
    canRetry = false,
    retrySeconds = 0,
    onretry,
    pending,
    stale,
    onstep,
    onbody,
    onwhy,
  }: {
    model: PlannerModel;
    bodies: PlanBodiesV1;
    facilities: PlanFacilityV1[];
    locations: PlannerLocation[];
    evaluation: PlanEvaluationV1 | null;
    view: 'now' | 'after_plan';
    error: string;
    canRetry?: boolean;
    retrySeconds?: number;
    onretry?: () => void;
    pending: boolean;
    stale: boolean;
    onstep: (id: string) => void;
    onbody: (id: string) => void;
    onwhy: (id: string) => void;
  } = $props();
  const totals = $derived(evaluation?.[view]);
  const firstBlocked = $derived(
    evaluation?.steps.find(
      (step) =>
        step.result === 'BLOCKED' &&
        model.sites.some((site) => site.site_id === step.site_id),
    ),
  );
  const unknownBodies = $derived([
    ...new Set(
      model.sites
        .filter(
          (site) =>
            site.body_ref !== null &&
            locations.some((location) => location.body_ref === site.body_ref) &&
            site.status !== 'demolished' &&
            locationSlots(model, bodies, site.body_ref)?.[
              `${site.slot_kind}_slots`
            ] == null,
        )
        .map((site) => site.body_ref!),
    ),
  ]);
  const building = $derived(
    model.sites.filter((site) => site.status === 'building'),
  );
  function name(id: string): string {
    const site = model.sites.find((site) => site.site_id === id);
    return (
      site?.name ||
      facilities.find((facility) => facility.id === site?.facility_id)?.name ||
      id
    );
  }
</script>

<aside
  class="plan-pane summary-pane"
  aria-labelledby="plan-summary-title"
  data-plan-summary
  aria-busy={pending}
>
  <div class="pane-heading">
    <h2 id="plan-summary-title">Summary</h2>
    <span>{view === 'now' ? 'Now' : 'After the plan'}</span>
  </div>
  {#if !model.sites.length}<p class="plan-muted">
      Add a Primary Port to evaluate this plan.
    </p>{/if}
  {#if error}<p class="plan-error" role="alert">{error}</p>{/if}
  {#if error && canRetry}<button
      type="button"
      disabled={pending || retrySeconds > 0}
      onclick={onretry}
      >{retrySeconds > 0
        ? `Try again in ${retrySeconds} s`
        : 'Try again'}</button
    >{/if}
  {#if pending}<p class="plan-muted" role="status">Evaluating plan…</p>{/if}
  {#if stale && evaluation}<p class="plan-muted">
      Last good evaluation — awaiting a valid current result.
    </p>{/if}
  <div class:stale={stale && !!evaluation}>
    {#if totals && evaluation}
      <dl class="plan-totals">
        <div class:negative={totals.t2 < 0}>
          <dt>Tier 2</dt>
          <dd>
            {totals.t2}{#if totals.t2 < 0}<small>Deficit</small>{/if}
          </dd>
        </div>
        <div class:negative={totals.t3 < 0}>
          <dt>Tier 3</dt>
          <dd>
            {totals.t3}{#if totals.t3 < 0}<small>Deficit</small>{/if}
          </dd>
        </div>
      </dl>
      <dl class="next-port">
        <div>
          <dt>Next Tier 2 port cost</dt>
          <dd>{evaluation.next_port_cost.t2} T2</dd>
        </div>
        <div>
          <dt>Next Tier 3 port cost</dt>
          <dd>{evaluation.next_port_cost.t3} T3</dd>
        </div>
      </dl>
      <div class="summary-results">
        <span><PlanIcon kind="OK" /> {evaluation.summary.OK} OK</span><span
          ><PlanIcon kind="BLOCKED" />
          {evaluation.summary.BLOCKED} Blocked</span
        ><span
          ><PlanIcon kind="NEEDS_INFO" />
          {evaluation.summary.NEEDS_INFO} Needs info</span
        >
      </div>
    {:else}<p class="plan-empty">
        The API evaluation will show your construction points here.
      </p>{/if}
  </div>
  <h3>Needs attention</h3>
  <ul class="attention-list">
    {#if firstBlocked}<li class:stale>
        <button type="button" onclick={() => onstep(firstBlocked.site_id)}
          >First blocked step: {name(firstBlocked.site_id)}</button
        >
      </li>{/if}
    {#each evaluation?.plan_checks ?? [] as check (check.code)}<li class:stale>
        {check.message} <small>{check.rule_ids.join(', ')}</small>
      </li>{/each}
    {#if unknownBodies.length}<li>
        <button type="button" onclick={() => onbody(unknownBodies[0])}
          >{unknownBodies.length}
          {unknownBodies.length === 1 ? 'body has' : 'bodies have'} builds with unknown
          slot counts</button
        >
      </li>{/if}
    {#if !firstBlocked && !evaluation?.plan_checks.length && !unknownBodies.length}<li
      >
        No items need attention.
      </li>{/if}
  </ul>
  <h3>Building now</h3>
  {#if building.length}<ul class="attention-list">
      {#each building as site (site.site_id)}<li>
          <button
            type="button"
            disabled={!evaluation?.steps.some(
              (step) => step.site_id === site.site_id,
            )}
            aria-label={`Building: ${name(site.site_id)}`}
            onclick={() => onwhy(site.site_id)}
            ><PlanIcon kind="building" /> {name(site.site_id)}</button
          >
        </li>{/each}
    </ul>{:else}<p class="plan-muted">No builds in progress.</p>{/if}
  {#if evaluation}<p class="plan-muted rulebook">
      Rulebook: {evaluation.rulebook_release}
    </p>{/if}
</aside>
