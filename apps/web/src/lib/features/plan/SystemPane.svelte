<script lang="ts">
  import { tick } from 'svelte';
  import {
    isTopmostPopout,
    registerPopout,
  } from '$lib/components/popout-stack';
  import type {
    PlanBodiesV1,
    PlanEvaluationV1,
    PlanFacilityV1,
  } from '$lib/api';
  import {
    buildBodyTree,
    flattenBodyTree,
    nonCurrentLocation,
    shortBodyName,
    type PlannerLocation,
  } from './locations';
  import {
    countLocationSlots,
    locationSlots,
    MAX_SITES,
    surfaceSlotPrediction,
    type PlannerModel,
    type PlannerSite,
  } from './model';
  import { resultLabel } from './presentation';
  import PlanIcon from './PlanIcon.svelte';
  import SlotEditor from './SlotEditor.svelte';
  import FacilityPicker from './FacilityPicker.svelte';
  import SystemSchematic from './SystemSchematic.svelte';
  import { buildSchematic, drawnRefResolver } from './schematic';

  let {
    bodies,
    model,
    systemName,
    facilities,
    evaluation,
    selectedBody,
    stale,
    hidden = false,
    onselectbody,
    onselectsite,
    onslot,
    onadd,
  }: {
    bodies: PlanBodiesV1;
    model: PlannerModel;
    systemName: string;
    facilities: PlanFacilityV1[];
    evaluation: PlanEvaluationV1 | null;
    selectedBody: string | null;
    stale: boolean;
    hidden?: boolean;
    onselectbody: (ref: string) => void;
    onselectsite: (id: string) => void;
    onslot: (
      ref: string,
      kind: 'orbital' | 'surface',
      value: number | null,
    ) => void;
    onadd: (facility: PlanFacilityV1, ref: string) => boolean | void;
  } = $props();
  let showNames = $state(true);
  let picker = $state<string | null>(null);
  const id = $props.id();
  const pickerId = `${id}-facility-picker`;
  let pickerTrigger = $state<HTMLButtonElement>();
  let pickerPanel = $state<HTMLDivElement>();
  let pickerLayer = $state(30);
  let pickerComponent = $state<{ dismissPreview: () => boolean }>();
  let addition = $state('');
  let pickerLeft = $state(8);
  let pickerTop = $state(8);
  // The visible height can be smaller than the layout viewport while a
  // mobile keyboard is open; the panel must fit what the commander can see.
  let pickerMaxHeight = $state<number | null>(null);
  const tree = $derived(flattenBodyTree(buildBodyTree(bodies)));
  const pickerLocation = $derived(
    tree.find((location) => location.body_ref === picker),
  );
  const schematic = $derived(buildSchematic(tree, model.sites, facilities));
  const schematicSelected = $derived(
    selectedBody ? drawnRefResolver(tree)(selectedBody) : null,
  );
  const facilityById = $derived(
    new Map(facilities.map((facility) => [facility.id, facility])),
  );
  const steps = $derived(
    new Map(evaluation?.steps.map((step) => [step.site_id, step]) ?? []),
  );
  const unplaced = $derived(
    model.sites.filter(
      (site) =>
        site.body_ref === null ||
        (!tree.some((body) => body.body_ref === site.body_ref) &&
          !nonCurrentLocation(bodies, site.body_ref)),
    ),
  );
  const nonCurrentSites = $derived(
    model.sites.filter((site) => nonCurrentLocation(bodies, site.body_ref)),
  );
  function lifecycleLabel(state: string): string {
    return state === 'RETIRED' ? 'retired' : 'missing from the latest data';
  }
  function features(
    location: PlannerLocation,
  ): { marker: string; label: string }[] {
    if (location.belt)
      return [location.belt.ring_type, location.belt.reserve]
        .filter((item): item is string => !!item)
        .map((label) => ({ marker: label[0], label }));
    const body = location.body!;
    return [
      body.is_landable === true ? { marker: 'L', label: 'Landable' } : null,
      body.has_rings ? { marker: '◎', label: 'Rings' } : null,
      body.geological_signals
        ? {
            marker: `G${body.geological_signals}`,
            label: `Geological ${body.geological_signals}`,
          }
        : null,
      body.biological_signals
        ? {
            marker: `B${body.biological_signals}`,
            label: `Biological ${body.biological_signals}`,
          }
        : null,
      body.volcanism && body.volcanism.toLowerCase() !== 'no volcanism'
        ? { marker: 'V', label: `Volcanism: ${body.volcanism}` }
        : null,
      body.terraforming_state &&
      body.terraforming_state.toLowerCase() !== 'not terraformable'
        ? { marker: 'T', label: `Terraformable: ${body.terraforming_state}` }
        : null,
      body.atmosphere && body.atmosphere.toLowerCase() !== 'no atmosphere'
        ? { marker: 'A', label: `Atmosphere: ${body.atmosphere}` }
        : null,
    ].filter((item): item is { marker: string; label: string } => !!item);
  }
  function discSize(radius: number | null): number {
    return radius === null
      ? 24
      : Math.min(28, Math.max(16, 10 + Math.log10(Math.max(1, radius)) * 4));
  }

  function positionPicker() {
    if (!pickerPanel || !pickerTrigger) return;
    const anchor = pickerTrigger.getBoundingClientRect();
    const panel = pickerPanel.getBoundingClientRect();
    const margin = 8;
    const viewport = window.visualViewport;
    const layoutWidth =
      document.documentElement.clientWidth || window.innerWidth;
    const layoutHeight =
      document.documentElement.clientHeight || window.innerHeight;
    const width = Math.min(viewport?.width ?? layoutWidth, layoutWidth);
    const height = Math.min(viewport?.height ?? layoutHeight, layoutHeight);
    const left = viewport?.offsetLeft ?? 0;
    const top = viewport?.offsetTop ?? 0;
    // A changed limit resizes the panel; its ResizeObserver repositions it.
    pickerMaxHeight = Math.max(0, Math.floor(height - margin * 2));
    const maxLeft = Math.max(
      left + margin,
      left + width - panel.width - margin,
    );
    const maxTop = Math.max(top + margin, top + height - panel.height - margin);
    if (width < 640) {
      pickerLeft = Math.max(left + margin, left + (width - panel.width) / 2);
      pickerTop = Math.max(top + margin, top + (height - panel.height) / 2);
      return;
    }
    const beside = anchor.right + margin;
    const below = anchor.bottom + margin;
    pickerLeft = Math.max(
      left + margin,
      Math.min(
        beside <= maxLeft ? beside : anchor.left - panel.width - margin,
        maxLeft,
      ),
    );
    pickerTop = Math.max(
      top + margin,
      Math.min(
        below <= maxTop ? below : anchor.top - panel.height - margin,
        maxTop,
      ),
    );
  }

  function closePicker() {
    picker = null;
    pickerTrigger?.focus({ preventScroll: true });
    void tick().then(() => {
      if (pickerTrigger?.disabled)
        document
          .getElementById('plan-tab-system-layout')
          ?.focus({ preventScroll: true });
    });
  }

  async function togglePicker(
    location: PlannerLocation,
    trigger: HTMLButtonElement,
  ) {
    if (picker === location.body_ref) {
      closePicker();
      return;
    }
    onselectbody(location.body_ref);
    pickerTrigger = trigger;
    picker = location.body_ref;
    await tick();
    if (picker !== location.body_ref || pickerTrigger !== trigger) return;
    positionPicker();
    pickerPanel
      ?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')
      ?.focus({ preventScroll: true });
  }

  function pickerKeydown(event: KeyboardEvent) {
    if (picker && isTopmostPopout(pickerPanel) && event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!pickerComponent?.dismissPreview()) closePicker();
    }
  }

  function outsideClick(event: MouseEvent) {
    if (
      picker &&
      isTopmostPopout(pickerPanel) &&
      event.target instanceof Node &&
      !pickerPanel?.contains(event.target) &&
      !pickerTrigger?.contains(event.target)
    )
      closePicker();
  }

  $effect(() => {
    if (hidden || !pickerLocation) picker = null;
  });

  $effect(() => {
    const panel = pickerPanel;
    if (!panel) return;
    positionPicker();
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(positionPicker);
    observer?.observe(panel);
    const scroll = (event: Event) => {
      // Scrolling the choices does not move their anchor.
      if (event.target instanceof Node && panel.contains(event.target)) return;
      positionPicker();
    };
    window.addEventListener('scroll', scroll, true);
    window.addEventListener('resize', positionPicker);
    window.visualViewport?.addEventListener('resize', positionPicker);
    window.visualViewport?.addEventListener('scroll', positionPicker);
    return () => {
      observer?.disconnect();
      window.removeEventListener('scroll', scroll, true);
      window.removeEventListener('resize', positionPicker);
      window.visualViewport?.removeEventListener('resize', positionPicker);
      window.visualViewport?.removeEventListener('scroll', positionPicker);
    };
  });

  $effect(() => {
    if (!picker || hidden || !pickerPanel) return;
    return registerPopout(pickerPanel, (value) => (pickerLayer = value));
  });
</script>

<svelte:window onkeydown={pickerKeydown} onclick={outsideClick} />

{#snippet siteGroups(sites: PlannerSite[], locationLabel: string)}
  {#each ['orbital', 'surface'] as kind (kind)}
    {@const groupSites = sites.filter((site) => site.slot_kind === kind)}
    {@const groupName = kind === 'orbital' ? 'Orbital' : 'Surface'}
    {#if groupSites.length}
      <div class="plan-site-group" class:surface={kind === 'surface'}>
        <h3 class="site-group-heading">
          <PlanIcon kind={kind === 'orbital' ? 'orbital' : 'surface'} />
          {groupName}
        </h3>
        <ul
          class="plan-sites"
          aria-label={`${groupName} sites ${locationLabel}`}
        >
          {#each groupSites as site (site.site_id)}
            {@const siteIndex = model.sites.indexOf(site)}
            {@const step = steps.get(site.site_id)}
            {@const facility = facilityById.get(site.facility_id)}
            {@const facilityName = facility?.name ?? site.facility_id}
            {@const name = showNames ? site.name : ''}
            {@const nonCurrent = nonCurrentLocation(bodies, site.body_ref)}
            {@const label = [
              `Step ${siteIndex + 1}`,
              ...(siteIndex === 0 ? ['Primary Port'] : []),
              site.status,
              ...(name ? [name] : []),
              facilityName,
              site.slot_kind,
              ...(site.status === 'planned' && step
                ? [resultLabel[step.result]]
                : []),
              ...(nonCurrent
                ? [
                    nonCurrent.name,
                    lifecycleLabel(nonCurrent.lifecycle_state),
                    'location no longer current',
                  ]
                : []),
            ].join(', ')}
            <li class="plan-site-row">
              <button
                type="button"
                class="plan-tile"
                class:blocked={step?.result === 'BLOCKED'}
                class:stale
                title={label}
                aria-label={label}
                onclick={() => onselectsite(site.site_id)}
              >
                <span class="tile-number"
                  >{#if siteIndex === 0}<span aria-hidden="true"
                      >★
                    </span>{/if}{siteIndex + 1}</span
                ><PlanIcon kind={site.status} />
                <span class="tile-label">
                  <strong class="site-name">{name || facilityName}</strong>
                  {#if name}<span class="site-type" title={facilityName}
                      >{facilityName}</span
                    >{/if}
                  {#if nonCurrent}<span
                      class="site-type"
                      title={`${nonCurrent.name} · ${lifecycleLabel(nonCurrent.lifecycle_state)}`}
                      >{nonCurrent.name} · {lifecycleLabel(
                        nonCurrent.lifecycle_state,
                      )}</span
                    >{/if}
                </span>
                {#if facility}<span class="plan-chip">T{facility.tier}</span
                  >{/if}
                {#if site.status === 'planned' && step}<PlanIcon
                    kind={step.result}
                  />{/if}
              </button>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/each}
{/snippet}

<div
  class="plan-pane system-pane"
  role="tabpanel"
  tabindex="-1"
  id="plan-system-layout"
  aria-labelledby="plan-tab-system-layout"
  {hidden}
  inert={hidden}
>
  <div class="pane-heading">
    <h2 id="plan-system-title">System</h2>
    <button
      type="button"
      aria-pressed={showNames}
      onclick={() => (showNames = !showNames)}
      >{showNames ? 'Names and types' : 'Types only'}</button
    >
  </div>
  <p class="plan-muted">
    Surface slots are predicted unless entered by you. Enter orbital slots from
    Architect Mode. Unknown totals are shown as ?.
  </p>
  {#if schematic.length}
    <SystemSchematic items={schematic} selectedRef={schematicSelected} />
  {/if}
  <ul class="plan-body-tree">
    {#each tree as location (location.body_ref)}
      {@const used = countLocationSlots(model, bodies, location.body_ref)}
      {@const slots = locationSlots(model, bodies, location.body_ref)}
      <li
        class="plan-body"
        class:selected={selectedBody === location.body_ref}
        class:child={location.depth > 0}
        style={`--body-depth: ${Math.min(location.depth, 4)}`}
        data-plan-body={location.body_ref}
      >
        <div class="body-heading compact-body-row">
          <span
            class="body-disc"
            class:star={location.body?.body_type?.toLowerCase() === 'star'}
            class:belt={location.kind === 'belt'}
            style={`--disc-size: ${discSize(location.radius_km)}px`}
            aria-hidden="true"
          ></span>
          <button
            type="button"
            class="body-name"
            title={location.name}
            aria-pressed={selectedBody === location.body_ref}
            onclick={() => onselectbody(location.body_ref)}
            >{shortBodyName(location.name, systemName)}</button
          >
          <div class="body-features">
            {#each features(location) as feature, featureIndex (featureIndex)}<span
                class="body-feature"
                role="img"
                title={feature.label}
                aria-label={feature.label}>{feature.marker}</span
              >{/each}
          </div>
          <div class="body-slots">
            <SlotEditor
              {hidden}
              label={location.name}
              kind="orbital"
              used={used.orbital_slots}
              value={slots?.orbital_slots ?? null}
              onchange={(value) => onslot(location.body_ref, 'orbital', value)}
            />
            {#if location.kind !== 'belt'}<SlotEditor
                {hidden}
                label={location.name}
                kind="surface"
                used={used.surface_slots}
                value={slots?.surface_slots ?? null}
                source={model.slots[location.body_ref]?.surface_slots != null
                  ? 'entered by you'
                  : 'predicted'}
                prediction={surfaceSlotPrediction(location.body)}
                disabled={location.is_landable === false}
                onchange={(value) =>
                  onslot(location.body_ref, 'surface', value)}
              />{/if}
          </div>
          <button
            type="button"
            class="add-build"
            aria-label={`Add a build on ${location.name}`}
            aria-haspopup="dialog"
            aria-expanded={picker === location.body_ref}
            aria-controls={picker === location.body_ref ? pickerId : undefined}
            disabled={model.sites.length >= MAX_SITES}
            onclick={(event) => togglePicker(location, event.currentTarget)}
            >+</button
          >
        </div>
        {@render siteGroups(
          model.sites.filter((site) => site.body_ref === location.body_ref),
          `on ${shortBodyName(location.name, systemName)}`,
        )}
      </li>
    {/each}
  </ul>
  {#if !tree.length}<p>No bodies are recorded for this system.</p>{/if}
  {#if nonCurrentSites.length}<h3 class="plan-muted">
      No longer in the galaxy data
    </h3>
    {@render siteGroups(
      nonCurrentSites,
      'on locations no longer in the galaxy data',
    )}{/if}
  {#if unplaced.length}<h3>Unplaced</h3>
    <p class="plan-muted">
      Choose a recorded location in the build's edit row.
    </p>
    {@render siteGroups(unplaced, 'in Unplaced')}{/if}
  {#if model.sites.length >= MAX_SITES}<p role="status">
      300-site limit reached. Remove a site before adding another.
    </p>{/if}
  <p
    class="plan-addition"
    role="status"
    aria-live="polite"
    aria-atomic="true"
    data-plan-addition
  >
    {addition}
  </p>
  {#if pickerLocation && !hidden}
    <div
      bind:this={pickerPanel}
      class="facility-picker-dialog"
      style:z-index={pickerLayer}
      id={pickerId}
      role="dialog"
      aria-modal="false"
      aria-labelledby={`${pickerId}-title`}
      tabindex="-1"
      style:left={`${pickerLeft}px`}
      style:top={`${pickerTop}px`}
      style:max-height={pickerMaxHeight === null
        ? undefined
        : `min(38rem, ${pickerMaxHeight}px)`}
    >
      {#key pickerLocation.body_ref}
        <FacilityPicker
          bind:this={pickerComponent}
          location={pickerLocation}
          {facilities}
          {model}
          {bodies}
          titleId={`${pickerId}-title`}
          onclose={closePicker}
          onadd={async (facility) => {
            const locationName = pickerLocation.name;
            if (onadd(facility, pickerLocation.body_ref) === false) return;
            closePicker();
            addition = '';
            await tick();
            addition = `${facility.name} added to plan on ${locationName}.`;
          }}
        />
      {/key}
    </div>
  {/if}
</div>

<style>
  .facility-picker-dialog {
    position: fixed;
    z-index: 30;
    display: flex;
    flex-direction: column;
    width: min(34rem, calc(100% - 1rem));
    max-height: min(38rem, calc(100dvh - 1rem));
    overflow: hidden;
    border: 1px solid var(--neon);
    border-radius: var(--radius);
    background: var(--panel);
    box-shadow: var(--shadow-panel);
  }
  .plan-addition {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .plan-body-tree {
    margin: 0.5rem 0 0;
  }
  .plan-body {
    position: relative;
    margin: 2px 0 2px calc(var(--body-depth) * 0.45rem);
    padding: 0 0 0 0.3rem;
    border-left: 1px solid transparent;
  }
  .plan-body.child {
    border-left-color: var(--line);
  }
  .plan-body.child::before {
    content: '';
    position: absolute;
    width: 0.3rem;
    border-top: 1px solid var(--line);
    top: 16px;
    left: 0;
  }
  .plan-body.selected {
    background: color-mix(in srgb, var(--neon) 4%, var(--panel));
    border-left-color: var(--neon);
    border-radius: var(--radius-sm);
  }
  .compact-body-row {
    display: flex;
    align-items: center;
    flex-wrap: nowrap;
    gap: 0.25rem;
    min-width: 0;
    min-height: 32px;
  }
  .body-disc {
    display: inline-block;
    width: var(--disc-size);
    height: var(--disc-size);
    flex: 0 0 var(--disc-size);
    border-radius: var(--radius-pill);
    background: radial-gradient(
      circle at 30% 25%,
      var(--neon),
      var(--line-strong) 65%,
      var(--panel-sunken)
    );
    border: 1px solid var(--neon);
  }
  .body-disc.star {
    background: radial-gradient(
      circle at 35% 30%,
      var(--warn),
      var(--accent-hi) 50%,
      var(--accent)
    );
    border-color: var(--accent-hi);
  }
  .body-disc.belt {
    background: repeating-radial-gradient(
      ellipse,
      var(--line) 0 2px,
      var(--text-muted) 3px 4px,
      transparent 5px 6px
    );
    transform: rotate(-25deg) scaleY(0.65);
    border: 2px dotted var(--text-muted);
  }
  :global(.plan-workspace) .body-name {
    min-width: 0;
    flex: 1;
    padding: 0.2rem 0;
    border: 0;
    background: transparent;
    text-align: left;
    font-size: 0.75rem;
    font-weight: 650;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .body-features,
  .body-slots {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    flex-wrap: nowrap;
    gap: 0.2rem;
  }
  .body-feature {
    color: var(--text-muted);
    font-size: 0.65rem;
    line-height: 1;
    white-space: nowrap;
  }
  :global(.plan-workspace) .add-build {
    flex: 0 0 24px;
    width: 24px;
    height: 24px;
    padding: 0;
    font-size: 1rem;
    line-height: 1;
  }
  @media (max-width: 600px) {
    .plan-body {
      margin-left: calc(var(--body-depth) * 0.3rem);
    }
    .compact-body-row {
      gap: 0.2rem;
    }
    .body-features {
      gap: 0.12rem;
    }
    .body-feature {
      font-size: 0.6rem;
    }
  }
</style>
