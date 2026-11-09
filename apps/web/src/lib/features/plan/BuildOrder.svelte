<script lang="ts">
  import type {
    PlanBodiesV1,
    PlanEvaluationV1,
    PlanFacilityV1,
  } from '$lib/api';
  import {
    facilityFitsLocation,
    locationLabel,
    nonCurrentLocation,
    shortBodyName,
    type PlannerLocation,
  } from './locations';
  import {
    canMoveSite,
    facilitySlotKind,
    primaryPortLocks,
    PRIMARY_PORT_PROTECTION,
    type PlannerModel,
    type PlannerSite,
  } from './model';
  import PlanIcon from './PlanIcon.svelte';
  import { resultLabel } from './presentation';

  let {
    model,
    facilities,
    locations,
    bodies,
    systemName,
    evaluation,
    stale,
    selectedBody,
    selectedSite,
    filter,
    onfilter,
    onwhy,
    onmove,
    onupdate,
    onremove,
    hidden = false,
    onclose,
  }: {
    model: PlannerModel;
    facilities: PlanFacilityV1[];
    locations: PlannerLocation[];
    bodies?: PlanBodiesV1;
    systemName: string;
    evaluation: PlanEvaluationV1 | null;
    stale: boolean;
    selectedBody: string | null;
    selectedSite: string | null;
    filter: string;
    onfilter: (filter: string) => void;
    onwhy: (id: string) => void;
    onmove: (id: string, direction: 'up' | 'down') => void;
    onupdate: (id: string, patch: Partial<PlannerSite>) => void;
    onremove: (id: string) => void;
    hidden?: boolean;
    onclose?: () => void;
  } = $props();
  const steps = $derived(
    new Map(evaluation?.steps.map((step) => [step.site_id, step]) ?? []),
  );
  const facilityById = $derived(
    new Map(facilities.map((facility) => [facility.id, facility])),
  );
  const locationById = $derived(
    new Map(locations.map((location) => [location.body_ref, location])),
  );
  const visible = $derived(
    model.sites.filter(
      (site) =>
        filter === 'All' ||
        (filter === 'body'
          ? site.body_ref === selectedBody
          : steps.get(site.site_id)?.result === filter),
    ),
  );
  function name(site: PlannerSite): string {
    return (
      site.name || facilityById.get(site.facility_id)?.name || site.facility_id
    );
  }
  function locationName(bodyRef: string | null): string {
    return locationLabel(bodyRef, locations, bodies, systemName);
  }
  function moveOptions(site: PlannerSite): PlannerLocation[] {
    const facility = facilityById.get(site.facility_id);
    return locations.filter(
      (location) =>
        (site.slot_kind !== 'surface' || location.is_landable !== false) &&
        (!facility || facilityFitsLocation(facility, location)),
    );
  }
  function facilityOptions(site: PlannerSite, index: number): PlanFacilityV1[] {
    const location = site.body_ref
      ? locationById.get(site.body_ref)
      : undefined;
    return facilities.filter(
      (facility) =>
        (index !== 0 || facility.is_port) &&
        (!location || facilityFitsLocation(facility, location)),
    );
  }
</script>

<div
  id="plan-build-order"
  class="plan-pane build-order-pane"
  role="tabpanel"
  tabindex="-1"
  aria-labelledby="plan-tab-build-order"
  data-plan-build-order
  {hidden}
  inert={hidden}
>
  <div class="pane-heading">
    <h2 id="plan-order-title" tabindex="-1">Build order</h2>
    <span>{model.sites.length} sites</span>
    {#if onclose}<button type="button" onclick={onclose}>Close</button>{/if}
  </div>
  <div class="plan-filters" role="group" aria-label="Filter build order">
    {#each [['All', 'All'], ['BLOCKED', 'Blocked'], ['NEEDS_INFO', 'Needs info'], ['body', `On ${selectedBody ? shortBodyName(locationById.get(selectedBody)?.name ?? selectedBody, systemName) : 'selected body'}`]] as [value, label] (value)}
      <button
        type="button"
        aria-pressed={filter === value}
        disabled={value === 'body' && selectedBody === null}
        onclick={() => onfilter(value)}>{label}</button
      >
    {/each}
  </div>
  {#if !model.sites.length}<p class="plan-empty">
      Start with a Primary Port. Use + beside a body to choose a build.
    </p>{/if}
  <ol class="build-order-list">
    {#each visible as site (site.site_id)}
      {@const index = model.sites.indexOf(site)}
      {@const step = steps.get(site.site_id)}
      {@const facility = facilityById.get(site.facility_id)}
      {@const locks = index === 0 ? primaryPortLocks(site) : null}
      {@const options = facilityOptions(site, index)}
      {@const nonCurrent = bodies
        ? nonCurrentLocation(bodies, site.body_ref)
        : null}
      <li
        value={index + 1}
        class="build-step compact-step"
        class:selected={selectedSite === site.site_id}
        class:blocked={step?.result === 'BLOCKED'}
        id={`plan-step-${site.site_id}`}
        data-plan-site={site.site_id}
        tabindex="-1"
      >
        <div class="step-title">
          <span class="step-number">{index + 1}</span>
          <h3 title={name(site)}>{name(site)}</h3>
          {#if index === 0}<span class="plan-chip primary-label"
              >Primary Port</span
            >{/if}
          <span
            class="step-description plan-muted"
            title={`${facility?.name ?? site.facility_id} · ${site.slot_kind} · ${locationName(site.body_ref)}`}
          >
            {facility?.name ?? site.facility_id} · {site.slot_kind} · {locationName(
              site.body_ref,
            )}
          </span>
          {#if step}<span
              class="plan-chip evaluation-value"
              class:stale
              data-result={step.result}
              ><PlanIcon kind={step.result} /> {resultLabel[step.result]}</span
            >{:else}<span class="step-awaiting plan-muted"
              >Awaiting evaluation</span
            >{/if}
        </div>
        <div class="step-detail-row">
          <div class="step-metrics">
            <span class="step-state"
              ><PlanIcon kind={site.status} /> {site.status}</span
            >
            {#if step}
              <div class="step-budget" class:stale>
                <span class="plan-chip"
                  >Cost {step.cost_t2} T2 / {step.cost_t3} T3</span
                >
                <span class="plan-chip"
                  >Earns {step.reward_t2} T2 / {step.reward_t3} T3</span
                >
                {#if step.counted_port_number !== null}<span class="plan-chip"
                    >port #{step.counted_port_number}</span
                  >{/if}
                <span class:negative={step.t2_after < 0 || step.t3_after < 0}
                  >After: {step.t2_after} T2 / {step.t3_after} T3</span
                >
              </div>
            {/if}
            <div class="step-actions">
              <button
                type="button"
                aria-label={`Move up ${name(site)}`}
                title={locks ? PRIMARY_PORT_PROTECTION : 'Move up'}
                disabled={!canMoveSite(model, site.site_id, 'up')}
                onclick={() => onmove(site.site_id, 'up')}
                ><span aria-hidden="true">↑</span></button
              >
              <button
                type="button"
                aria-label={`Move down ${name(site)}`}
                title={locks ? PRIMARY_PORT_PROTECTION : 'Move down'}
                disabled={!canMoveSite(model, site.site_id, 'down')}
                onclick={() => onmove(site.site_id, 'down')}
                ><span aria-hidden="true">↓</span></button
              >
              <button
                type="button"
                aria-label={`Why? ${name(site)}`}
                disabled={!step}
                onclick={() => onwhy(site.site_id)}>Why?</button
              >
            </div>
          </div>
          <details class="step-edit">
            <summary aria-label={`Edit ${name(site)}`}
              >Edit<span class="plan-sr-only"> {name(site)}</span></summary
            >
            <div class="step-edit-fields">
              <label
                >Facility <select
                  aria-label={`Facility for step ${index + 1}`}
                  value={site.facility_id}
                  disabled={locks?.facility ?? false}
                  onchange={(event) => {
                    const chosen = facilityById.get(event.currentTarget.value);
                    if (chosen)
                      onupdate(site.site_id, {
                        facility_id: chosen.id,
                        slot_kind: facilitySlotKind(chosen),
                      });
                  }}
                >
                  {#if !options.some((item) => item.id === site.facility_id)}<option
                      value={site.facility_id}
                      disabled
                      >{facility?.name ?? site.facility_id} — choose a valid facility</option
                    >{/if}
                  {#each ['orbital', 'surface'] as kind (kind)}
                    {@const group = options.filter(
                      (option) => facilitySlotKind(option) === kind,
                    )}
                    {#if group.length}
                      <optgroup
                        label={kind === 'orbital' ? 'Orbital' : 'Surface'}
                      >
                        {#each group as option (option.id)}<option
                            value={option.id}>{option.name}</option
                          >{/each}
                      </optgroup>
                    {/if}
                  {/each}
                </select></label
              >
              <label
                >Name <input
                  maxlength="64"
                  aria-label={`Name for step ${index + 1}`}
                  value={site.name}
                  oninput={(event) =>
                    onupdate(site.site_id, { name: event.currentTarget.value })}
                /></label
              >
              <label
                >Status <select
                  aria-label={`Status for step ${index + 1}`}
                  disabled={locks?.status ?? false}
                  value={site.status}
                  onchange={(event) =>
                    onupdate(site.site_id, {
                      status: event.currentTarget
                        .value as PlannerSite['status'],
                    })}
                >
                  {#each locks?.allowedStatuses ?? ['planned', 'building', 'complete', 'demolished'] as status (status)}<option
                      value={status}>{status}</option
                    >{/each}
                </select></label
              >
              <label
                >Move to body <select
                  aria-label={`Body for step ${index + 1}`}
                  disabled={locks?.body ?? false}
                  value={site.body_ref ?? ''}
                  onchange={(event) =>
                    onupdate(site.site_id, {
                      body_ref: event.currentTarget.value || null,
                    })}
                >
                  {#if nonCurrent}<option value={site.body_ref} disabled
                      >{locationName(site.body_ref)} — location no longer current</option
                    >{/if}
                  <option value="">Unplaced</option
                  >{#each moveOptions(site) as location (location.body_ref)}<option
                      value={location.body_ref}
                      >{shortBodyName(location.name, systemName)}</option
                    >{/each}
                </select></label
              >
              {#if locks?.remove}<p>{PRIMARY_PORT_PROTECTION}</p>{:else}<button
                  type="button"
                  class="remove-build"
                  aria-label={`Remove ${name(site)}`}
                  onclick={() => onremove(site.site_id)}>Remove build</button
                >{/if}
            </div>
          </details>
        </div>
      </li>
    {/each}
  </ol>
  {#if model.sites.length && !visible.length}<p>
      No builds match this filter.
    </p>{/if}
</div>

<style>
  .build-order-list {
    margin: 0;
  }
  .compact-step {
    display: grid;
    gap: 0.125rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    margin-top: 0.25rem;
    padding: 0.25rem;
    scroll-margin-top: 0.5rem;
  }
  .build-step.selected {
    border-color: var(--neon);
  }
  .step-title,
  .step-metrics,
  .step-budget,
  .step-actions,
  .step-state,
  .evaluation-value {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    min-width: 0;
    flex-wrap: nowrap;
  }
  .step-title {
    height: 1.5rem;
  }
  .step-title h3 {
    flex: 0 1 auto;
    min-width: 2rem;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.75rem;
    line-height: 1.2;
  }
  .step-number {
    display: grid;
    place-items: center;
    flex: 0 0 1.25rem;
    height: 1.25rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    font-size: 0.7rem;
    font-variant-numeric: tabular-nums;
  }
  .primary-label {
    flex: 0 0 auto;
    color: var(--accent);
    font-size: 0.7rem;
  }
  .step-description {
    flex: 1 1 5rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.7rem;
  }
  .evaluation-value,
  .step-awaiting {
    flex: 0 0 auto;
    margin-left: auto;
    white-space: nowrap;
    font-size: 0.7rem;
  }
  .step-detail-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    gap: 0.25rem;
    min-width: 0;
    font-size: 0.7rem;
    line-height: 1.25;
  }
  .step-metrics {
    min-height: 1.5rem;
    flex-wrap: wrap;
  }
  .step-state {
    text-transform: capitalize;
    flex-shrink: 0;
  }
  .step-budget {
    flex-wrap: wrap;
    font-variant-numeric: tabular-nums;
  }
  .step-budget > span {
    white-space: nowrap;
  }
  .step-budget .plan-chip {
    padding: 0.1rem 0.2rem;
    font-size: inherit;
    line-height: inherit;
  }
  .step-actions {
    margin-left: auto;
    flex-shrink: 0;
  }
  .step-actions button,
  .step-edit summary {
    box-sizing: border-box;
    display: inline-flex;
    justify-content: center;
    align-items: center;
    min-width: 1.5rem;
    min-height: 1.5rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    padding: 0.125rem 0.25rem;
    font-size: 0.7rem;
    line-height: 1.25;
    cursor: pointer;
  }
  .step-edit {
    min-width: 0;
  }
  .step-edit[open] {
    grid-column: 1 / -1;
  }
  .step-edit summary {
    position: relative;
    list-style: none;
  }
  .step-edit summary::-webkit-details-marker {
    display: none;
  }
  .step-edit summary:hover {
    border-color: var(--line-strong);
    box-shadow: var(--line-glow);
  }
  .step-edit-fields {
    border-top: 1px solid var(--line);
    margin-top: 0.5rem;
    padding: 0 0.25rem 0.25rem;
    font-size: 0.8rem;
  }
  .step-edit label {
    display: grid;
    gap: 0.35rem;
    margin: 0.7rem 0;
    font-size: 0.85rem;
  }
  .step-edit select {
    width: 100%;
  }
  @media (max-width: 480px) {
    .step-title {
      height: auto;
      min-height: 1.5rem;
      flex-wrap: wrap;
    }
    .step-title h3 {
      /* The name keeps the first line with its number and wraps rather than being cut. */
      flex: 1 1 calc(100% - 2.5rem);
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .step-description {
      flex-basis: 100%;
      white-space: normal;
    }
    .step-actions {
      margin-left: 0;
    }
  }
</style>
