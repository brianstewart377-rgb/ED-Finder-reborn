<script lang="ts">
  import { tick } from 'svelte';
  import type {
    PlanBodiesV1,
    PlanEvaluationV1,
    PlanFacilityV1,
    PlanIssueV1,
  } from '$lib/api';
  import InspectPopout from '$lib/components/InspectPopout.svelte';
  import {
    locationAliases,
    locationSlots,
    surfaceSlotPrediction,
    type PlannerModel,
  } from './model';
  import { locationLabel, type PlannerLocation } from './locations';
  import PlanIcon from './PlanIcon.svelte';
  import { resultLabel } from './presentation';

  let {
    model,
    bodies,
    facilities,
    locations,
    evaluation,
    systemName,
    stale,
    pending,
    error,
    issues = [],
    canNavigate = true,
    open,
    trigger,
    onclose,
    onexport,
    onstep,
    onbody,
  }: {
    model: PlannerModel;
    bodies: PlanBodiesV1 | undefined;
    facilities: PlanFacilityV1[];
    locations: PlannerLocation[];
    evaluation: PlanEvaluationV1 | null;
    systemName: string;
    stale: boolean;
    pending: boolean;
    error: string;
    issues?: PlanIssueV1[];
    canNavigate?: boolean;
    open: boolean;
    trigger?: HTMLButtonElement;
    onclose: () => void;
    onexport: () => void;
    onstep: (id: string) => void;
    onbody: (ref: string, kind?: 'orbital' | 'surface') => void;
  } = $props();

  const counts = $derived(
    ['planned', 'building', 'complete', 'demolished'].map((status) => ({
      status,
      count: model.sites.filter((site) => site.status === status).length,
    })),
  );
  const slots = $derived(
    locations.flatMap((location) => {
      const totals = locationSlots(model, bodies, location.body_ref);
      const kinds: ('orbital' | 'surface')[] =
        location.kind === 'belt' || location.is_landable === false
          ? ['orbital']
          : ['orbital', 'surface'];
      return kinds
        .filter((kind) => totals?.[`${kind}_slots`] == null)
        .map((kind) => ({ location, kind }));
    }),
  );
  const predictions = $derived(
    locations.filter(
      (location) =>
        surfaceSlotPrediction(location.body) !== null &&
        !locationAliases(bodies, location.body_ref).some(
          (ref) => model.slots[ref]?.surface_slots != null,
        ),
    ),
  );
  const missingBodies = $derived(
    locations
      .map((location) => ({
        location,
        notes:
          evaluation?.economies.notes.filter(
            (note) =>
              /^(?:unknown|missing)_/i.test(note.code) &&
              note.body_ref !== null &&
              locationAliases(bodies, location.body_ref).includes(
                note.body_ref,
              ),
          ) ?? [],
      }))
      .filter(
        ({ location, notes }) =>
          location.body?.missing_inputs?.length ||
          (location.body?.prediction_status === 'unknown' &&
            location.is_landable !== false &&
            location.body.prediction_reason) ||
          notes.length,
      ),
  );
  const unresolvedSteps = $derived(
    evaluation?.steps.filter(
      (step) =>
        (step.result === 'BLOCKED' || step.result === 'NEEDS_INFO') &&
        model.sites.some((site) => site.site_id === step.site_id),
    ) ?? [],
  );
  const invalidBuilds = $derived(
    model.sites.flatMap((site, index) => {
      const reported = issues.filter((issue) => issue.site_id === site.site_id);
      return reported.length ? [{ site, index, reported }] : [];
    }),
  );
  const unresolvedCount = $derived(
    slots.length +
      predictions.length +
      missingBodies.length +
      unresolvedSteps.length +
      invalidBuilds.length,
  );

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'build-order', label: 'Build order' },
    { id: 'export', label: 'Export' },
  ] as const;
  type ReviewTab = (typeof TABS)[number]['id'];

  let tab = $state<ReviewTab>('overview');
  let tablist = $state<HTMLDivElement>();
  let unresolvedList = $state<HTMLUListElement>();

  // Every time the pop-out is opened it starts on the Overview.
  $effect(() => {
    if (!open) tab = 'overview';
  });

  function activateTab(next: ReviewTab): void {
    // Focus before hiding the outgoing panel, including pointer activation in
    // browsers that do not focus buttons on click.
    tablist
      ?.querySelector<HTMLButtonElement>(`[data-review-tab="${next}"]`)
      ?.focus({ preventScroll: true });
    tab = next;
  }

  function navigateTabs(event: KeyboardEvent, current: ReviewTab): void {
    const index = TABS.findIndex((item) => item.id === current);
    let next: number;
    switch (event.key) {
      case 'ArrowRight':
        next = (index + 1) % TABS.length;
        break;
      case 'ArrowLeft':
        next = (index + TABS.length - 1) % TABS.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = TABS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    activateTab(TABS[next].id);
  }

  /** Move to the first unresolved item that can be opened, if any. */
  async function goToFirstItem(): Promise<void> {
    if (tab !== 'overview') {
      tab = 'overview';
      await tick();
    }
    unresolvedList
      ?.querySelector<HTMLButtonElement>('button:not(:disabled)')
      ?.focus();
  }

  const buildRows = $derived(
    model.sites.map((site, index) => ({
      site,
      index,
      step: evaluation?.steps.find((step) => step.site_id === site.site_id),
    })),
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

<InspectPopout
  id="plan-review-export"
  title="Review & export"
  kind="review"
  {open}
  {trigger}
  {onclose}
  closeOnOutside={false}
  describedby="plan-review-description"
>
  <p id="plan-review-description" class="plan-muted">
    Review the current plan's checks, rules and unknowns. Export a DRAFT copy to
    keep in a file.
  </p>
  {#if unresolvedCount > 0}
    <div class="review-banner" data-review-banner>
      <PlanIcon kind="NEEDS_INFO" />
      <p>
        <strong
          >{unresolvedCount} unresolved {unresolvedCount === 1
            ? 'item'
            : 'items'}</strong
        >
        <span>Each one is listed below with its source.</span>
      </p>
      {#if canNavigate}
        <button type="button" class="quiet-button" onclick={goToFirstItem}
          >Go to first item</button
        >
      {/if}
    </div>
  {/if}
  <div
    bind:this={tablist}
    role="tablist"
    aria-label="Review sections"
    class="plan-task-tabs review-tabs"
  >
    {#each TABS as item (item.id)}
      <button
        id={`plan-review-tab-${item.id}`}
        type="button"
        role="tab"
        data-review-tab={item.id}
        aria-selected={tab === item.id}
        aria-controls={`plan-review-panel-${item.id}`}
        tabindex={tab === item.id ? 0 : -1}
        onclick={() => activateTab(item.id)}
        onkeydown={(event) => navigateTabs(event, item.id)}>{item.label}</button
      >
    {/each}
  </div>
  <!-- Every panel stays mounted so the unresolved list keeps its focus targets. -->
  <div
    id="plan-review-panel-overview"
    role="tabpanel"
    aria-labelledby="plan-review-tab-overview"
    hidden={tab !== 'overview'}
    inert={tab !== 'overview'}
  >
    <div class="review-layout">
      <section
        class="review-summary"
        aria-labelledby="plan-review-summary-title"
        aria-busy={pending}
      >
        <h3 id="plan-review-summary-title">Plan summary</h3>
        <p>
          <strong>{systemName}</strong> · ID64 <code>{model.system_id64}</code>
        </p>
        <p>{model.sites.length} plan items</p>
        <ul class="review-item-counts" aria-label="Plan item counts">
          {#each counts as item (item.status)}
            <li>{item.count} {item.status}</li>
          {/each}
        </ul>
        {#if !model.sites.length}<p class="plan-muted">
            Add a Primary Port to evaluate this plan.
          </p>{/if}
        {#if error}<p class="plan-error" role="alert">{error}</p>{/if}
        {#if pending}<p class="plan-muted" role="status">
            Evaluating plan…
          </p>{/if}
        {#if stale && evaluation}<p class="plan-muted">
            Last good evaluation — awaiting a valid current result.
          </p>{/if}
        <div class:stale={stale && !!evaluation}>
          {#if evaluation}
            {#each ['now', 'after_plan'] as view (view)}
              {@const totals = evaluation[view as 'now' | 'after_plan']}
              <section aria-label={view === 'now' ? 'Now' : 'After the plan'}>
                <h4>{view === 'now' ? 'Now' : 'After the plan'}</h4>
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
              </section>
            {/each}
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
              <span><PlanIcon kind="OK" /> {evaluation.summary.OK} OK</span>
              <span
                ><PlanIcon kind="BLOCKED" />
                {evaluation.summary.BLOCKED} Blocked</span
              >
              <span
                ><PlanIcon kind="NEEDS_INFO" />
                {evaluation.summary.NEEDS_INFO} Needs info</span
              >
            </div>
          {:else}
            <p class="plan-empty">
              The API evaluation will show your construction points here.
            </p>
          {/if}
        </div>
      </section>

      <div class="review-details">
        <section aria-labelledby="plan-review-unresolved-title">
          <h3 id="plan-review-unresolved-title">
            Unresolved information ({unresolvedCount})
          </h3>
          <p class="plan-muted">
            Choose an item to focus its body, slot editor or build.
          </p>
          {#if !canNavigate}<p class="plan-muted">
              Body and build editors are unavailable until planner data loads
              successfully.
            </p>{/if}
          {#if !bodies}<p class="plan-muted">
              System body data is unavailable; body and slot information cannot
              be reviewed yet.
            </p>{/if}
          <ul
            class="review-unresolved-list"
            aria-label="Unresolved information"
            bind:this={unresolvedList}
          >
            {#each slots as { location, kind } (`${location.body_ref}:${kind}`)}
              <li>
                <button
                  type="button"
                  data-review-kind="unknown-slot"
                  disabled={!canNavigate}
                  data-body-ref={location.body_ref}
                  data-slot-kind={kind}
                  onclick={() => onbody(location.body_ref, kind)}
                  >Unknown {kind} slots: {location.name}</button
                >
              </li>
            {/each}
            {#each missingBodies as { location, notes } (location.body_ref)}
              <li>
                <button
                  type="button"
                  data-review-kind="missing-data"
                  disabled={!canNavigate}
                  data-body-ref={location.body_ref}
                  onclick={() => onbody(location.body_ref)}
                  >Missing body data: {location.name}</button
                >
                {#if location.body?.missing_inputs?.length}<small
                    >Missing inputs: {location.body.missing_inputs.join(
                      ', ',
                    )}</small
                  >{/if}
                {#if location.body?.prediction_reason}<small
                    >{location.body.prediction_reason}</small
                  >{/if}
                {#each notes as note, index (index)}<small class:stale
                    >{note.message}</small
                  >{/each}
              </li>
            {/each}
            {#each predictions as location (location.body_ref)}
              <li>
                <button
                  type="button"
                  data-review-kind="prediction"
                  disabled={!canNavigate}
                  data-body-ref={location.body_ref}
                  data-slot-kind="surface"
                  onclick={() => onbody(location.body_ref, 'surface')}
                  >Prediction-only surface slots: {location.name}</button
                >
                <small
                  >{`Source: predicted${location.body?.prediction_version ? ` · ${location.body.prediction_version}` : ''}. Verify in Architect Mode.`}</small
                >
              </li>
            {/each}
            {#each unresolvedSteps as step (step.site_id)}
              <li class:stale>
                <button
                  type="button"
                  data-review-kind="build"
                  disabled={!canNavigate}
                  data-site-id={step.site_id}
                  onclick={() => onstep(step.site_id)}
                  >{step.result === 'BLOCKED'
                    ? 'Blocked build'
                    : 'Build needs information'}: {name(step.site_id)}
                  (step {model.sites.findIndex(
                    (site) => site.site_id === step.site_id,
                  ) + 1})</button
                >
                {#each step.reasons as reason, index (index)}<small
                    >{reason}</small
                  >{/each}
                {#if step.rule_ids.length}<small
                    >Rules: {step.rule_ids.join(', ')}</small
                  >{/if}
              </li>
            {/each}
            {#each invalidBuilds as { site, index, reported } (site.site_id)}
              <li>
                <button
                  type="button"
                  data-review-kind="invalid-build"
                  disabled={!canNavigate}
                  data-site-id={site.site_id}
                  onclick={() => onstep(site.site_id)}
                  >Invalid build: {name(site.site_id)} (step {index +
                    1})</button
                >
                {#each reported as issue, issueIndex (issueIndex)}
                  <small>{issue.message} ({issue.code})</small>
                {/each}
              </li>
            {/each}
            {#if !unresolvedCount && bodies}<li>
                No unresolved information is listed in the current data.
              </li>{/if}
          </ul>
        </section>
        {#if evaluation?.plan_checks.length}
          <section aria-label="Plan checks" class:stale>
            <h3>Plan checks</h3>
            <ul class="review-unresolved-list">
              {#each evaluation.plan_checks as check (check.code)}
                <li>
                  {check.message}<small>{check.rule_ids.join(', ')}</small>
                </li>
              {/each}
            </ul>
          </section>
        {/if}
      </div>
    </div>
  </div>
  <div
    id="plan-review-panel-build-order"
    role="tabpanel"
    aria-labelledby="plan-review-tab-build-order"
    hidden={tab !== 'build-order'}
    inert={tab !== 'build-order'}
  >
    <h3>Build order</h3>
    {#if buildRows.length}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll the table sideways on narrow screens.) -->
      <div
        class="review-table-wrap"
        class:stale={stale && !!evaluation}
        role="region"
        aria-label="Planned builds table"
        tabindex="0"
      >
        <table class="review-build-table">
          <caption class="plan-sr-only">
            Planned builds in order, with their location, status and check
            result
          </caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Build</th>
              <th scope="col">Location</th>
              <th scope="col">Status</th>
              <th scope="col">Check</th>
            </tr>
          </thead>
          <tbody>
            {#each buildRows as { site, index, step } (site.site_id)}
              <tr>
                <td>{index + 1}</td>
                <th scope="row">{name(site.site_id)}</th>
                <td
                  >{locationLabel(
                    site.body_ref,
                    locations,
                    bodies,
                    systemName,
                  )}</td
                >
                <td class="review-status"
                  ><PlanIcon kind={site.status} /> {site.status}</td
                >
                <td
                  >{#if step}<PlanIcon kind={step.result} />
                    {resultLabel[step.result]}{:else}Not evaluated{/if}</td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="plan-empty">No builds are in this plan yet.</p>
    {/if}
  </div>
  <div
    id="plan-review-panel-export"
    role="tabpanel"
    aria-labelledby="plan-review-tab-export"
    hidden={tab !== 'export'}
    inert={tab !== 'export'}
  >
    <h3>Before you export</h3>
    <ul class="review-notes">
      <li>
        Your plan stays in this browser. Exporting saves a separate copy as a
        file.
      </li>
      <li>
        The file holds the system ID64, your planned builds in order with their
        status, and the slot counts you have entered. Slot counts you have not
        entered stay unknown.
      </li>
      <li>
        The file leaves out the catalogue facts, the points on the Overview and
        the unresolved items.
      </li>
      <li>
        It is saved as <code>colony-plan-DRAFT-{model.system_id64}.json</code>
        and can be brought back with Import plan.
      </li>
    </ul>
  </div>
  <div class="review-footer">
    {#if evaluation}<p class="plan-muted rulebook">
        Rulebook: {evaluation.rulebook_release}
      </p>{/if}
    <button
      type="button"
      class="primary-button"
      aria-label="Export DRAFT plan"
      onclick={onexport}>Export DRAFT plan</button
    >
  </div>
</InspectPopout>
