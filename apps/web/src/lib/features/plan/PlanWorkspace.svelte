<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { onDestroy, tick } from 'svelte';
  import { resolve } from '$app/paths';
  import {
    ApiError,
    evaluateV3Plan,
    getV3PlanCatalogue,
    listV3PlanBodies,
    getPlanSystem,
    type PlanEvaluationV1,
    type PlanFacilityV1,
    type PlanInvalidV1,
    type PlanIssueV1,
  } from '$lib/api';
  import type { Id64 } from '$lib/domain/id64';
  import { colonyProjects } from '$lib/persistence/stores';
  import { resolvePlanLocations } from './locations';
  import { parseRavenLayout } from './raven-import';
  import { ravenToPlannerModel } from './raven-apply';
  import {
    emptyPlan,
    recordToPlanner,
    plannerToRecord,
    findPlannerProject,
    toPlanRequest,
    addSite,
    insertSiteBefore,
    moveSite,
    removeSite,
    updateSite,
    abandonPlan,
    isProtectedPrimary,
    setSlotCount,
    findFreeLocation,
    exportPlan,
    importPlan,
    replacePlan,
    rejectInvalidLocations,
    unplaceMissingLocations,
    MAX_SITES,
    MAX_IMPORT_BYTES,
    type PlannerModel,
    type PlannerSite,
  } from './model';
  import SystemPane from './SystemPane.svelte';
  import BuildOrder from './BuildOrder.svelte';
  import SummaryPane from './SummaryPane.svelte';
  import ReviewExport from './ReviewExport.svelte';
  import WhyDrawer from './WhyDrawer.svelte';
  import EconomyBars from './EconomyBars.svelte';
  import { PLAN_TABS, type PlanTab } from './tabs';
  import {
    economyRuleIds,
    type EconomyWhySelection,
  } from './economy-presentation';
  import './plan.css';

  let {
    systemId,
    activeTab = $bindable('system-layout'),
    ontabchange,
  }: {
    systemId: Id64;
    activeTab?: PlanTab;
    ontabchange?: (tab: PlanTab) => void;
  } = $props();
  const catalogue = createQuery(() => ({
    queryKey: ['ed-finder', 'plan-catalogue'],
    queryFn: ({ signal }) => getV3PlanCatalogue(signal),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  }));
  const bodies = createQuery(() => ({
    queryKey: ['ed-finder', 'plan-bodies', systemId],
    queryFn: ({ signal }) => listV3PlanBodies(systemId, signal),
    retry: false,
  }));
  const identity = createQuery(() => ({
    queryKey: ['ed-finder', 'plan-system', systemId],
    queryFn: ({ signal }) => getPlanSystem(systemId, signal),
    staleTime: Infinity,
    retry: false,
  }));
  const record = $derived(
    findPlannerProject($colonyProjects.value.state.projects, systemId),
  );
  const model = $derived(
    record ? recordToPlanner(record) : emptyPlan(systemId),
  );
  const primaryProtected = $derived(
    !!model.sites[0] && isProtectedPrimary(model, model.sites[0].site_id),
  );
  const systemName = $derived(
    identity.data?.name ??
      (typeof record?.system_name === 'string'
        ? record.system_name
        : `System ${systemId}`),
  );
  const facilities = $derived(catalogue.data?.facilities ?? []);
  const plannerDataAvailable = $derived(
    !!bodies.data &&
      !bodies.isPending &&
      !bodies.isError &&
      !catalogue.isPending &&
      !catalogue.isError,
  );
  const locations = $derived(
    bodies.data ? resolvePlanLocations(bodies.data) : [],
  );
  const older = $derived(
    record?.build_plan_placements.filter(
      (item) => item.schema !== 'cpe_site_v1',
    ) ?? [],
  );
  let evaluation = $state<PlanEvaluationV1 | null>(null);
  let evaluatedRequest = $state('');
  let evaluationError = $state('');
  let evaluationIssues = $state<PlanIssueV1[]>([]);
  let canRetry = $state(false);
  let retryAt = $state(0);
  let retrySeconds = $state(0);
  let retryEvaluation = $state<(() => void) | null>(null);
  let actionError = $state('');
  let pending = $state(false);
  let announcement = $state('');
  let view = $state<'now' | 'after_plan'>('after_plan');
  let selectedBody = $state<string | null>(null);
  let selectedSite = $state<string | null>(null);
  let whySite = $state<string | null>(null);
  let whyEconomy = $state<EconomyWhySelection | null>(null);
  let filter = $state('All');
  let fileInput = $state<HTMLInputElement>();
  let ravenFileInput = $state<HTMLInputElement>();
  let importOpen = $state(false);
  let importMenu = $state<HTMLDetailsElement>();

  /** A file picker opens from the menu, which then closes behind it. */
  function chooseImport(input: HTMLInputElement | undefined): void {
    importOpen = false;
    input?.click();
  }

  function importKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape' || !importOpen) return;
    // Only the menu closes; an open tool keeps its own Escape.
    event.stopPropagation();
    importOpen = false;
    importMenu?.querySelector('summary')?.focus();
  }

  function importOutside(event: MouseEvent): void {
    if (importOpen && importMenu && !importMenu.contains(event.target as Node))
      importOpen = false;
  }
  let reviewOpen = $state(false);
  let reviewTrigger = $state<HTMLButtonElement>();
  let importNotes = $state<{ code: string; detail: string }[]>([]);
  let active = true;
  onDestroy(() => {
    active = false;
  });
  const request = $derived(
    bodies.data ? toPlanRequest(model, bodies.data) : null,
  );
  const requestKey = $derived(request ? JSON.stringify(request) : '');
  const stale = $derived(
    requestKey !== evaluatedRequest || !!evaluationError || pending,
  );
  const whyStep = $derived(
    evaluation?.steps.find((step) => step.site_id === whySite),
  );
  const whyBuild = $derived(
    model.sites.find(
      (site) => site.site_id === (whyEconomy?.siteId ?? whySite),
    ),
  );
  const economyRules = $derived(
    evaluation && whyEconomy
      ? economyRuleIds(
          whyEconomy.economyName,
          evaluation.economies.now.find(
            (port) => port.site_id === whyEconomy?.siteId,
          )?.economies ?? [],
          evaluation.economies.after_plan.find(
            (port) => port.site_id === whyEconomy?.siteId,
          )?.economies ?? [],
        )
      : [],
  );

  function selectTab(tab: PlanTab, focus = false): void {
    if (activeTab !== tab) {
      activeTab = tab;
      ontabchange?.(tab);
    }
    if (focus)
      document
        .getElementById(`plan-tab-${tab}`)
        ?.focus({ preventScroll: true });
  }
  function tabKeydown(event: KeyboardEvent, tab: PlanTab): void {
    const index = PLAN_TABS.findIndex((item) => item.id === tab);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? PLAN_TABS.length - 1
          : event.key === 'ArrowRight'
            ? (index + 1) % PLAN_TABS.length
            : event.key === 'ArrowLeft'
              ? (index + PLAN_TABS.length - 1) % PLAN_TABS.length
              : null;
    if (next === null) return;
    event.preventDefault();
    selectTab(PLAN_TABS[next].id, true);
  }
  const panelScroll: Record<PlanTab, number> = {
    'system-layout': 0,
    'build-order': 0,
  };
  let renderedTab: PlanTab | undefined;
  $effect.pre(() => {
    // URL/history changes can hide the panel that currently owns focus.
    const tab = activeTab;
    if (renderedTab === tab) return;
    if (renderedTab)
      panelScroll[renderedTab] =
        document.getElementById(`plan-${renderedTab}`)?.scrollTop ?? 0;
    renderedTab = tab;
    const other = tab === 'system-layout' ? 'build-order' : 'system-layout';
    if (
      document.getElementById(`plan-${other}`)?.contains(document.activeElement)
    )
      document
        .getElementById(`plan-tab-${tab}`)
        ?.focus({ preventScroll: true });
    void tick().then(() => {
      if (!active || activeTab !== tab) return;
      const panel = document.getElementById(`plan-${tab}`);
      if (panel) panel.scrollTop = panelScroll[tab];
    });
  });

  $effect(() => {
    const deadline = retryAt;
    if (!deadline) {
      retrySeconds = 0;
      return;
    }
    const update = () => {
      retrySeconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    };
    update();
    const timer = setInterval(() => {
      update();
      if (retrySeconds === 0) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  });

  function tryAgain(): void {
    if (canRetry && !pending && Date.now() >= retryAt) retryEvaluation?.();
  }

  $effect(() => {
    const snapshot = request;
    const fingerprint = requestKey;
    canRetry = false;
    retryAt = 0;
    retryEvaluation = null;
    if (!snapshot || !$colonyProjects.hydrated) return;
    if (snapshot.sites.length === 0) {
      pending = false;
      evaluationError = '';
      evaluationIssues = [];
      announcement = '';
      return;
    }
    const controller = new AbortController();
    let current = true;
    pending = true;
    evaluationError = '';
    evaluationIssues = [];
    announcement = '';
    const evaluate = (): void => {
      pending = true;
      evaluationError = '';
      evaluationIssues = [];
      canRetry = false;
      retryAt = 0;
      void evaluateV3Plan(snapshot, controller.signal)
        .then((result) => {
          if (!current) return;
          evaluation = result;
          evaluatedRequest = fingerprint;
          announcement = `${result.summary.BLOCKED} blocked steps. ${result.summary.NEEDS_INFO} steps need information.`;
        })
        .catch((error: unknown) => {
          if (!current) return;
          const invalid =
            error instanceof ApiError && error.status === 400
              ? (error.body as PlanInvalidV1)
              : null;
          evaluationIssues =
            invalid?.error === 'plan_invalid' && Array.isArray(invalid.issues)
              ? invalid.issues
              : [];
          evaluationError = evaluationIssues.length
            ? evaluationIssues.map((issue) => issue.message).join(' ')
            : 'The plan could not be evaluated. Try again shortly.';
          const status = error instanceof ApiError ? error.status : 0;
          canRetry =
            status === 0 || status === 429 || (status >= 500 && status < 600);
          const seconds =
            error instanceof Error && 'retryAfterSeconds' in error
              ? error.retryAfterSeconds
              : null;
          if (canRetry && typeof seconds === 'number' && seconds > 0)
            retryAt = Date.now() + seconds * 1000;
        })
        .finally(() => {
          if (current) pending = false;
        });
    };
    retryEvaluation = evaluate;
    const timer = setTimeout(evaluate, 250);
    return () => {
      current = false;
      clearTimeout(timer);
      controller.abort();
    };
  });

  function change(action: (current: PlannerModel) => PlannerModel): boolean {
    try {
      const next = action(model);
      const projects = $colonyProjects.value;
      const nextRecord = plannerToRecord(next, record, { systemName });
      const saved = colonyProjects.set({
        ...projects,
        state: {
          ...projects.state,
          projects: { ...projects.state.projects, [nextRecord.id]: nextRecord },
        },
      });
      actionError = saved
        ? ''
        : 'This browser could not save the plan. Export a copy before leaving.';
      return saved;
    } catch (error) {
      actionError =
        error instanceof Error
          ? error.message
          : 'The plan could not be changed.';
      return false;
    }
  }
  function makeSite(facility: PlanFacilityV1, ref: string | null): PlannerSite {
    return {
      schema: 'cpe_site_v1',
      site_id: crypto.randomUUID(),
      facility_id: facility.id,
      body_ref: ref,
      slot_kind: facility.allowed_location.includes('surface')
        ? 'surface'
        : 'orbital',
      status: 'planned',
      name: '',
    };
  }
  function add(facility: PlanFacilityV1, ref: string): boolean {
    const site = makeSite(facility, ref);
    change((current) => addSite(current, site));
    // Storage failures can retain a usable in-memory plan. Announce only if
    // the site actually exists; a rejected model/version change adds nothing.
    const added = model.sites.some((item) => item.site_id === site.site_id);
    if (added) selectedSite = site.site_id;
    return added;
  }
  function isPort(facilityId: string): boolean {
    return facilities.some(
      (facility) => facility.id === facilityId && facility.is_port,
    );
  }
  async function abandon(): Promise<void> {
    if (!model.sites.length || primaryProtected) return;
    if (
      !window.confirm(
        `Abandon this plan? All ${model.sites.length} sites for ${systemName} will be removed, as if the claim were cancelled. Slot counts are kept.`,
      )
    )
      return;
    if (!change(abandonPlan)) return;
    await tick();
    if (active) announcement = 'Plan abandoned.';
  }
  async function jumpToStep(id: string): Promise<void> {
    selectTab('build-order');
    selectedSite = id;
    filter = 'All';
    await tick();
    if (!active) return;
    const step = document.getElementById(`plan-step-${id}`);
    step?.focus();
    step?.scrollIntoView?.({ block: 'nearest' });
  }
  function showWhy(id: string): void {
    whyEconomy = null;
    selectedSite = id;
    filter = 'All';
    whySite = id;
  }
  function showEconomyWhy(selection: EconomyWhySelection): void {
    whySite = null;
    whyEconomy = selection;
  }
  async function closeWhy(): Promise<void> {
    whySite = null;
    whyEconomy = null;
    await tick();
    // A task change can hide the opener that the drawer would return to.
    if (
      active &&
      (document.activeElement === document.body ||
        document.activeElement?.closest('[hidden]'))
    ) {
      document.getElementById(`plan-tab-${activeTab}`)?.focus();
    }
  }
  async function selectBody(
    ref: string,
    kind?: 'orbital' | 'surface',
  ): Promise<void> {
    selectedBody = ref;
    selectTab('system-layout');
    await tick();
    if (!active) return;
    const row = [
      ...document.querySelectorAll<HTMLElement>('[data-plan-body]'),
    ].find((element) => element.dataset.planBody === ref);
    const target = row?.querySelector<HTMLElement>(
      kind ? `[data-slot-kind="${kind}"]` : '.body-name',
    );
    target?.focus();
    target?.scrollIntoView?.({ block: 'nearest' });
  }
  function insertPayer(facilityId: string): void {
    const facility = facilities.find((item) => item.id === facilityId);
    if (!facility || !whySite) return;
    const before = whySite;
    change((current) =>
      insertSiteBefore(
        current,
        makeSite(
          facility,
          findFreeLocation(current, locations, facility, bodies.data),
        ),
        before,
      ),
    );
  }
  function download(): void {
    const url = URL.createObjectURL(
      new Blob([exportPlan(model)], { type: 'application/json' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `colony-plan-DRAFT-${systemId}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function upload(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    try {
      if (file.size > MAX_IMPORT_BYTES)
        throw new Error('Plan files must be no larger than 2 MB.');
      const text = await file.text();
      if (!active) return;
      // Validate the entire file before presenting a replacement confirmation.
      let candidate = importPlan(text, systemId, { confirmOtherSystem: true });
      const source = (JSON.parse(text) as { system_id64: string }).system_id64;
      if (!bodies.data)
        throw new Error(
          'Wait for the system bodies to load before importing a plan.',
        );
      if (candidate.sites[0] && !isPort(candidate.sites[0].facility_id))
        throw new Error(
          "This plan's first site is not a port, so it cannot be the Primary Port.",
        );
      if (source !== systemId) {
        if (
          !window.confirm(
            `This file belongs to system ${source}. Import its sites and slot counts into ${systemName} (${systemId})? Missing or non-current locations will become Unplaced. A completed Primary Port without a location here will stay editable until you give it one.`,
          )
        )
          return;
        candidate = unplaceMissingLocations(candidate, bodies.data);
      }
      rejectInvalidLocations(candidate, bodies.data);
      if (
        !window.confirm(
          `Replace this system's planner sites and slot counts with ${candidate.sites.length} imported sites? Older plan items will be kept.`,
        )
      )
        return;
      change((current) => replacePlan(current, candidate));
    } catch (error) {
      actionError =
        error instanceof Error
          ? error.message
          : 'The plan file could not be read.';
    }
  }
  async function uploadRaven(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    try {
      if (file.size > MAX_IMPORT_BYTES)
        throw new Error('Plan files must be no larger than 2 MB.');
      const text = await file.text();
      if (!active) return;
      let raw: unknown;
      try {
        raw = JSON.parse(text, (key: string, value: unknown) => {
          // Reject rounded numeric identifiers at the file boundary. String
          // identifiers retain their exact digits through JSON decoding.
          if (
            ['id64', 'id', 'num', 'bodyNum'].includes(key) &&
            typeof value === 'number' &&
            !Number.isSafeInteger(value)
          )
            throw new Error(
              `This Raven layout contains a numeric ${key} that is not a safe integer; its exact digits cannot be imported. Use a string for a large system or site id.`,
            );
          return value;
        });
      } catch (error) {
        if (error instanceof SyntaxError)
          throw new Error(
            'This file is not a Raven Colonial layout (it is not valid JSON).',
          );
        throw error;
      }
      const result = parseRavenLayout(raw);
      if (!result.ok || !result.system)
        throw new Error(
          result.errors[0]?.detail ??
            'This file is not a Raven Colonial layout.',
        );
      if (!bodies.data)
        throw new Error(
          'Wait for the system bodies to load before importing a plan.',
        );
      if (result.system.id64 !== systemId)
        throw new Error(
          `This Raven layout is for ${result.system.name} (${result.system.id64}). Open that system's planner to import it.`,
        );
      if (!catalogue.data)
        throw new Error(
          'Wait for the planner catalogue to load before importing a Raven layout.',
        );
      const { model: candidate, warnings } = ravenToPlannerModel(
        result,
        bodies.data,
        facilities,
      );
      if (candidate.sites[0] && !isPort(candidate.sites[0].facility_id))
        throw new Error(
          "This plan's first site is not a port, so it cannot be the Primary Port.",
        );
      rejectInvalidLocations(candidate, bodies.data);
      const count = candidate.sites.length;
      const unplaced = candidate.sites.filter(
        (site) => site.body_ref === null,
      ).length;
      if (
        !window.confirm(
          `Replace this system's planner sites and slot counts with ${count} sites from Raven Colonial? ${unplaced} will be Unplaced and ${warnings.length} items need checking. The first site becomes the Primary Port.`,
        )
      )
        return;
      if (!change((current) => replacePlan(current, candidate))) return;
      importNotes = warnings;
      await tick();
      if (active) announcement = `${count} sites imported from Raven Colonial.`;
    } catch (error) {
      actionError =
        error instanceof Error
          ? error.message
          : 'The Raven layout file could not be read.';
    }
  }
</script>

<svelte:window onclick={importOutside} />

<main class="plan-workspace product-page">
  <header class="plan-header">
    <nav aria-label="Planner breadcrumbs">
      <a href={resolve('/explore')}>Explore</a><span aria-hidden="true">›</span
      ><a href={resolve(`/inspect?system=${systemId}`)}>Inspect</a><span
        aria-hidden="true">›</span
      ><span aria-current="page">Plan</span>
    </nav>
    <div class="plan-title">
      <div>
        <p class="eyebrow">Colony planner</p>
        <h1>{systemName}</h1>
        <p class="plan-muted">
          ID64 <code>{systemId}</code> · Kept in this browser
        </p>
      </div>
      <div class="plan-header-actions">
        <div class="plan-view" role="group" aria-label="Points view">
          <button
            type="button"
            aria-pressed={view === 'now'}
            onclick={() => (view = 'now')}>Now</button
          ><button
            type="button"
            aria-pressed={view === 'after_plan'}
            onclick={() => (view = 'after_plan')}>After the plan</button
          >
        </div>
        <button
          bind:this={reviewTrigger}
          type="button"
          class="plan-review-button"
          aria-haspopup="dialog"
          aria-expanded={reviewOpen}
          aria-controls={reviewOpen ? 'plan-review-export' : undefined}
          onclick={() => (reviewOpen = !reviewOpen)}>Review &amp; export</button
        ><button type="button" aria-label="Export DRAFT plan" onclick={download}
          >Export DRAFT plan</button
        >
        <details
          class="plan-import"
          bind:open={importOpen}
          bind:this={importMenu}
        >
          <summary onkeydown={importKeydown}>Import</summary>
          <div class="plan-import-panel">
            <button
              type="button"
              onkeydown={importKeydown}
              onclick={() => chooseImport(fileInput)}>Import plan</button
            >
            <button
              type="button"
              onkeydown={importKeydown}
              onclick={() => chooseImport(ravenFileInput)}
              >Import from Raven</button
            >
          </div>
        </details>
        <button
          type="button"
          disabled={!model.sites.length || primaryProtected}
          title={primaryProtected
            ? 'A completed Primary Port can never be removed (RB-0008).'
            : undefined}
          onclick={abandon}>Abandon plan</button
        ><input
          bind:this={fileInput}
          type="file"
          accept="application/json,.json"
          aria-label="Import plan file"
          class="plan-file-input"
          onchange={upload}
        /><input
          bind:this={ravenFileInput}
          type="file"
          accept="application/json,.json"
          aria-label="Import Raven Colonial layout file"
          class="plan-file-input"
          onchange={uploadRaven}
        />
      </div>
    </div>
  </header>
  <p class="plan-sr-only" aria-live="polite" aria-atomic="true">
    {announcement}
  </p>
  {#if importNotes.length}
    <details class="plan-pane plan-import-notes">
      <summary>Import notes ({importNotes.length})</summary>
      <ul>
        {#each importNotes as note, index (index)}
          <li>{note.detail}</li>
        {/each}
      </ul>
      <button type="button" onclick={() => (importNotes = [])}>Dismiss</button>
    </details>
  {/if}
  {#if actionError}<p class="plan-error" role="alert">{actionError}</p>{/if}
  {#if $colonyProjects.diagnostic}<p class="plan-error" role="alert">
      Browser storage: {$colonyProjects.diagnostic.detail}. Export your plan to
      keep a copy.
    </p>{/if}
  {#if bodies.isPending || catalogue.isPending}<p role="status">
      Loading planner catalogue and system bodies…
    </p>
  {:else if bodies.isError || catalogue.isError}<section class="plan-pane">
      <h2>Planner data unavailable</h2>
      <p role="alert">The catalogue or system bodies could not be loaded.</p>
      <button
        type="button"
        onclick={() => {
          void bodies.refetch();
          void catalogue.refetch();
        }}>Try again</button
      >
    </section>
  {:else if bodies.data}
    <div class="plan-task-tabs" role="tablist" aria-label="Plan tasks">
      {#each PLAN_TABS as tab (tab.id)}
        <button
          type="button"
          role="tab"
          id={`plan-tab-${tab.id}`}
          aria-label={tab.label}
          aria-selected={activeTab === tab.id}
          aria-controls={`plan-${tab.id}`}
          tabindex={activeTab === tab.id ? 0 : -1}
          onclick={() => selectTab(tab.id, true)}
          onkeydown={(event) => tabKeydown(event, tab.id)}
        >
          {tab.label}
          {#if tab.id === 'build-order'}
            <span>({model.sites.length})</span>
            {#if evaluation && !stale && model.sites.length > 0 && evaluation.summary.BLOCKED > 0}
              <span class="build-order-blocked"
                >· {evaluation.summary.BLOCKED} blocked</span
              >
            {/if}
          {/if}
        </button>
      {/each}
    </div>
    <div class="plan-columns">
      <SystemPane
        hidden={activeTab !== 'system-layout'}
        bodies={bodies.data}
        {model}
        {systemName}
        {facilities}
        {evaluation}
        {selectedBody}
        {stale}
        onselectbody={(ref) => (selectedBody = ref)}
        onselectsite={showWhy}
        onslot={(ref, kind, value) =>
          change((current) => setSlotCount(current, ref, kind, value))}
        onadd={add}
      />
      <BuildOrder
        hidden={activeTab !== 'build-order'}
        bodies={bodies.data}
        {model}
        {facilities}
        {locations}
        {systemName}
        {evaluation}
        {stale}
        {selectedBody}
        {selectedSite}
        {filter}
        onfilter={(value) => (filter = value)}
        onwhy={showWhy}
        onmove={(id, direction) =>
          change((current) => moveSite(current, id, direction))}
        onupdate={(id, patch) =>
          change((current) => updateSite(current, id, patch))}
        onremove={(id) => change((current) => removeSite(current, id))}
      />
      <SummaryPane
        {model}
        bodies={bodies.data}
        {facilities}
        {locations}
        {evaluation}
        {view}
        error={evaluationError}
        {canRetry}
        {retrySeconds}
        onretry={tryAgain}
        {pending}
        {stale}
        onstep={jumpToStep}
        onbody={selectBody}
        onwhy={showWhy}
      />
    </div>
    <EconomyBars
      {model}
      {facilities}
      bodies={bodies.data}
      {evaluation}
      {stale}
      onwhy={showEconomyWhy}
    />
  {/if}
  <ReviewExport
    open={reviewOpen}
    trigger={reviewTrigger}
    {model}
    bodies={bodies.data}
    {facilities}
    {locations}
    {systemName}
    {evaluation}
    {pending}
    {stale}
    error={evaluationError}
    issues={evaluationIssues}
    canNavigate={plannerDataAvailable}
    onclose={() => (reviewOpen = false)}
    onexport={download}
    onstep={jumpToStep}
    onbody={selectBody}
  />
  {#if older.length}<details class="plan-pane older-items">
      <summary>Older plan items (read-only) · {older.length}</summary>
      <ul>
        {#each older as item, index (index)}<li>
            <strong>Older item {index + 1}</strong>
            <pre>{JSON.stringify(item, null, 2)}</pre>
          </li>{/each}
      </ul>
    </details>{/if}
  {#if whyEconomy && whyBuild && evaluation}
    <WhyDrawer
      name={whyBuild.name ||
        facilities.find((item) => item.id === whyBuild.facility_id)?.name ||
        whyBuild.facility_id}
      economy={{ name: whyEconomy.economyName, ruleIds: economyRules }}
      {evaluation}
      {stale}
      onclose={closeWhy}
    />
  {:else if whyStep && whyBuild && evaluation}
    <WhyDrawer
      step={whyStep}
      index={model.sites.indexOf(whyBuild)}
      name={whyBuild.name ||
        facilities.find((item) => item.id === whyBuild.facility_id)?.name ||
        whyBuild.facility_id}
      {evaluation}
      {facilities}
      {stale}
      onclose={closeWhy}
      oninsert={insertPayer}
      canInsert={!stale &&
        model.sites.length < MAX_SITES &&
        model.sites.indexOf(whyBuild) > 0}
    />
  {/if}
</main>
