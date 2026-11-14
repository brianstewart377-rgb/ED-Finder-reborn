<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { tick } from 'svelte';
  import {
    ApiError,
    getSystem,
    listV3PlanBodies,
    type SystemDetailBody,
    type SystemDetailRing,
    type SystemDetailStation,
  } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import {
    bodyTypeLabel,
    stellarClassification,
  } from '$lib/spatial/system-scene';
  import SystemMap from '$lib/system-map/SystemMap.svelte';
  import SystemMapLegend from '$lib/system-map/SystemMapLegend.svelte';
  import { buildTree, systemFromDetail } from '$lib/system-map/tree';
  import {
    builtHere,
    groundSlotsInWords,
    orbitsInWords,
    stationInWords,
  } from '$lib/system-map/words';
  import { INSPECT_TABS, type InspectTab } from './inspect-tabs';
  import SystemRatings from './SystemRatings.svelte';
  import SourcesFreshness from './SourcesFreshness.svelte';
  import {
    AVAILABILITY_LABELS,
    type RatingsSourceState,
  } from './source-freshness';
  import {
    bodySignalSummary,
    hotspotSummary,
    isFleetCarrier,
    percent,
    titleFromCode,
  } from './system-detail-format';

  let {
    id64,
    headingId = 'system-detail-title',
    autofocusHeading = false,
    tab = 'overview',
    onTabChange,
  } = $props<{
    id64: Id64;
    headingId?: string;
    autofocusHeading?: boolean;
    tab?: InspectTab;
    onTabChange?: (tab: InspectTab) => void;
  }>();
  const tabId = $props.id();
  let tablist = $state<HTMLDivElement>();
  let overviewPanel = $state<HTMLDivElement>();
  let bodiesPanel = $state<HTMLDivElement>();
  let stationsPanel = $state<HTMLDivElement>();

  function focusTab(next: InspectTab): void {
    tablist
      ?.querySelector<HTMLButtonElement>(`[data-inspect-tab="${next}"]`)
      ?.focus({ preventScroll: true });
  }

  function activateTab(next: InspectTab): void {
    // Focus before hiding the outgoing panel, including pointer activation in
    // browsers that do not focus buttons on click.
    focusTab(next);
    if (tab === next) return;
    tab = next;
    onTabChange?.(next);
  }

  function navigateTabs(event: KeyboardEvent, current: InspectTab): void {
    const index = INSPECT_TABS.findIndex((item) => item.value === current);
    let next: number;
    switch (event.key) {
      case 'ArrowRight':
        next = (index + 1) % INSPECT_TABS.length;
        break;
      case 'ArrowLeft':
        next = (index + INSPECT_TABS.length - 1) % INSPECT_TABS.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = INSPECT_TABS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    activateTab(INSPECT_TABS[next].value);
  }

  $effect.pre(() => {
    const active = tab;
    // URL/history changes can hide a panel while focus is still inside it.
    const focused = document.activeElement;
    const panels = {
      overview: overviewPanel,
      bodies: bodiesPanel,
      stations: stationsPanel,
    };
    for (const item of INSPECT_TABS) {
      if (item.value !== active && panels[item.value]?.contains(focused)) {
        focusTab(active);
        break;
      }
    }
  });

  let heading = $state<HTMLHeadingElement>();
  let lastFocusedId: Id64 | null = null;
  let selection = $state<{
    systemId: Id64;
    bodyKey: string;
    name: string;
  }>();
  const selectionAnnouncement = $derived(
    selection && selection.systemId === id64
      ? `Selected ${selection.name}`
      : '',
  );
  let ratingsSource = $state<RatingsSourceState | null>(null);
  const currentRatingsSource = $derived(
    ratingsSource?.id64 === id64 ? ratingsSource : null,
  );

  function updateRatingsSource(source: RatingsSourceState): void {
    if (source.id64 === id64) ratingsSource = source;
  }

  const detail = createQuery(() => ({
    queryKey: queryKeys.system(id64),
    queryFn: ({ signal }) => getSystem(id64, signal),
    retry: (failureCount, error) =>
      !(error instanceof ApiError && isAbsent(error.status)) &&
      failureCount < 1,
  }));
  // A selection belongs to one system. Guard every consumer before any
  // render, including while the query is switching to the next system.
  const selectedBodyKey = $derived(
    selection && selection.systemId === id64 && detail.data?.id64 === id64
      ? selection.bodyKey
      : undefined,
  );
  // 404: no such system in the published catalogue. 422: an id64 beyond the
  // catalogue's signed 64-bit key space, which therefore cannot exist there.
  function isAbsent(status: number): boolean {
    return status === 404 || status === 422;
  }
  const notFound = $derived(
    detail.error instanceof ApiError && isAbsent(detail.error.status),
  );
  const coordinates = $derived.by(() => {
    if (!detail.data) return null;
    const { x, y, z } = detail.data;
    if (x == null || y == null || z == null) return AVAILABILITY_LABELS.unknown;
    return `${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)} ly`;
  });
  const mainStar = $derived(
    detail.data?.bodies.find((body) => body.isMainStar === true) ?? null,
  );
  const bodyCount = $derived.by(() => {
    if (!detail.data) return null;
    const { bodies, sourceBodyCount } = detail.data;
    // Only flag an incomplete catalogue: the source's own body total can lag
    // behind the bodies actually loaded, which is not a gap.
    return sourceBodyCount != null && bodies.length < sourceBodyCount
      ? `${integer(bodies.length)} of ${integer(sourceBodyCount)} known`
      : integer(bodies.length);
  });
  const catalogueUpdated = $derived.by(() => {
    const value = detail.data?.sourceUpdatedAt;
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? null
      : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(
          date,
        );
  });
  // The builder's list of the same bodies adds each one's temperature and
  // predicted ground slots. It is asked for once the Bodies tab is opened;
  // the map is drawn without it until it comes, and without it if it fails.
  const planBodies = createQuery(() => ({
    queryKey: queryKeys.planBodies(String(id64)),
    queryFn: ({ signal }) => listV3PlanBodies(String(id64), signal),
    enabled: tab === 'bodies',
    retry: false,
  }));
  const mapTree = $derived.by(() => {
    const system = detail.data;
    if (!system || system.id64 !== id64) return null;
    const plan =
      planBodies.data?.system_id64 === String(id64) ? planBodies.data : null;
    return buildTree(systemFromDetail(system, plan));
  });
  const selectedBody = $derived(
    detail.data?.bodies.find((body) => body.bodyKey === selectedBodyKey) ??
      null,
  );
  const selectedNode = $derived(
    selectedBodyKey && mapTree
      ? (mapTree.nodes.get(selectedBodyKey) ?? null)
      : null,
  );
  /** Bodies the map placed by their name or distance: our data gives no parent. */
  const guessedBodies = $derived(
    mapTree
      ? [...mapTree.nodes.values()].filter(
          (node) => node.guessedParent !== false && node.type !== 'barycentre',
        )
      : [],
  );
  const selectedRings = $derived(
    selectedBody?.rings.filter((ring) => ring.kind === 'RING').length ?? 0,
  );
  const selectedBelts = $derived(
    selectedBody?.rings.filter((ring) => ring.kind === 'BELT').length ?? 0,
  );
  const bodyNames = $derived(
    new Map(
      (detail.data?.bodies ?? []).map((body) => [body.bodyKey, body.name]),
    ),
  );
  // Busy systems can hold dozens of fleet carriers, and the catalogue only has
  // a snapshot of where they were, so they sit behind a toggle.
  const ports = $derived(
    (detail.data?.stations ?? []).filter(
      (station) => !isFleetCarrier(station.stationType),
    ),
  );
  const carriers = $derived(
    (detail.data?.stations ?? []).filter((station) =>
      isFleetCarrier(station.stationType),
    ),
  );

  $effect.pre(() => {
    // Leaving a system ends the visit; returning must not restore its pick.
    // Refetches of the same system leave the selection intact.
    if (selection && selection.systemId !== id64) selection = undefined;
  });

  $effect(() => {
    if (!autofocusHeading || !detail.data || lastFocusedId === id64) return;
    lastFocusedId = id64;
    void tick().then(() => heading?.focus({ preventScroll: true }));
  });

  const integer = (value: number | null | undefined) =>
    typeof value === 'number'
      ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(
          value,
        )
      : null;

  function bodyClass(body: SystemDetailBody): string {
    if (body.bodyType === 'planet' && body.bodyClass != null)
      return body.bodyClass;
    const label = bodyTypeLabel(body.bodyType) ?? 'Unknown body type';
    const classification = stellarClassification(body);
    return classification ? `${label} · ${classification}` : label;
  }

  /** `Metallic · Pristine reserves`; null when neither is catalogued. */
  function ringFacts(ring: SystemDetailRing): string | null {
    const reserve = titleFromCode(ring.reserveLevel);
    const facts = [
      titleFromCode(ring.ringType),
      reserve ? `${reserve} reserves` : null,
    ].filter((fact): fact is string => fact !== null);
    return facts.length > 0 ? facts.join(' · ') : null;
  }

  function ringHotspots(ring: SystemDetailRing): string {
    const summary = hotspotSummary(ring.hotspots);
    if (summary) return `Hotspots: ${summary}`;
    return ring.signalsComplete === true
      ? 'No hotspots'
      : 'Hotspots not recorded';
  }

  /** `Coriolis Starport · 500 ls · Reference System 3`. */
  function stationFacts(station: SystemDetailStation): string {
    const body = station.bodyKey ? bodyNames.get(station.bodyKey) : undefined;
    return [
      titleFromCode(station.stationType) ?? 'Type not recorded',
      station.distanceFromArrivalLs != null
        ? `${integer(station.distanceFromArrivalLs)} ls`
        : null,
      body ?? null,
    ]
      .filter((fact): fact is string => fact !== null)
      .join(' · ');
  }

  /**
   * `Extraction (primary) · Refinery (secondary)`; null when none is
   * catalogued. A share appears only if the catalogue holds one: the Spansh
   * import deliberately stores designations without shares.
   */
  function stationEconomies(station: SystemDetailStation): string | null {
    if (station.economies.length === 0) return null;
    return station.economies
      .map((economy) => {
        const name = titleFromCode(economy.economy) ?? economy.economy;
        const share =
          economy.weight != null ? ` ${percent(economy.weight)}` : '';
        const role = economy.isPrimary
          ? ' (primary)'
          : economy.isSecondary
            ? ' (secondary)'
            : '';
        return `${name}${share}${role}`;
      })
      .join(' · ');
  }

  function selectBody(bodyKey: string): void {
    const system = detail.data;
    if (!system || system.id64 !== id64) return;
    const body = system.bodies.find((body) => body.bodyKey === bodyKey);
    if (!body) return;
    // Capture the announcement on selection, not on catalogue refetches.
    selection = { systemId: id64, bodyKey, name: body.name };
  }
</script>

<section
  class="system-detail"
  data-system-id64={id64}
  aria-busy={detail.isPending}
>
  <p class="system-detail-announcement" aria-live="polite" aria-atomic="true">
    {selectionAnnouncement}
  </p>
  {#if detail.isPending}
    <p class="eyebrow">Inspect</p>
    <h1 id={headingId}>Loading system…</h1>
    <p class="state-copy" role="status">
      Retrieving catalogue detail for <code>{id64}</code>.
    </p>
  {:else if detail.isError}
    <p class="eyebrow">Inspect</p>
    <h1 id={headingId}>
      {notFound ? 'System not found' : 'System detail unavailable'}
    </h1>
    <p
      class="state-copy"
      role="alert"
      data-unavailable-fields
      data-availability={notFound ? 'unknown' : 'unavailable'}
    >
      {#if notFound}
        <strong>{AVAILABILITY_LABELS.unknown}</strong> — No catalogue system
        exists for the exact identifier <code>{id64}</code>.
      {:else}
        <strong>{AVAILABILITY_LABELS.unavailable}</strong> — ED-Finder could not load
        this system. Your selected-system context is unchanged.
      {/if}
    </p>
    <button
      class="secondary-button"
      type="button"
      onclick={() => detail.refetch()}>Retry</button
    >
  {:else if detail.data}
    <p class="eyebrow">System detail</p>
    <h1 id={headingId} tabindex="-1" bind:this={heading}>
      {detail.data.name}
    </h1>
    <p class="system-identity">ID64 <code>{detail.data.id64}</code></p>

    <div bind:this={tablist} role="tablist" aria-label="Inspect sections">
      {#each INSPECT_TABS as item (item.value)}
        <button
          id={`${tabId}-tab-${item.value}`}
          type="button"
          role="tab"
          data-inspect-tab={item.value}
          aria-selected={tab === item.value}
          aria-controls={`${tabId}-panel-${item.value}`}
          tabindex={tab === item.value ? 0 : -1}
          onclick={() => activateTab(item.value)}
          onkeydown={(event) => navigateTabs(event, item.value)}
          >{item.label}</button
        >
      {/each}
    </div>

    <!-- Keep every query, map, selection and scroll container mounted. -->
    <div
      bind:this={overviewPanel}
      id={`${tabId}-panel-overview`}
      role="tabpanel"
      aria-labelledby={`${tabId}-tab-overview`}
      tabindex="0"
      hidden={tab !== 'overview'}
      inert={tab !== 'overview'}
    >
      <dl class="detail-grid">
        <div
          data-unavailable-fields={coordinates === AVAILABILITY_LABELS.unknown
            ? ''
            : undefined}
          data-availability={coordinates === AVAILABILITY_LABELS.unknown
            ? 'unknown'
            : undefined}
        >
          <dt>Coordinates</dt>
          <dd>{coordinates}</dd>
        </div>
        {#if detail.data.lifecycleState !== 'ACTIVE'}<div
            data-system-lifecycle={detail.data.lifecycleState}
          >
            <dt>Catalogue status</dt>
            <dd>
              {detail.data.lifecycleState === 'RETIRED'
                ? 'Retired from the catalogue'
                : 'Missing from the latest catalogue source'}
            </dd>
          </div>{/if}
        <div
          data-unavailable-fields={!mainStar ? '' : undefined}
          data-availability={!mainStar ? 'unknown' : undefined}
        >
          <dt>Main star</dt>
          <dd>
            {mainStar
              ? (stellarClassification(mainStar) ?? 'Star (class not recorded)')
              : AVAILABILITY_LABELS.unknown}
          </dd>
        </div>
        <div>
          <dt>Bodies returned</dt>
          <dd>{bodyCount}</dd>
        </div>
        <div>
          <dt>Stations returned</dt>
          <dd>{integer(detail.data.stations.length)}</dd>
        </div>
        <div>
          <dt>Catalogue updated</dt>
          <dd>
            {#if catalogueUpdated}
              <time datetime={detail.data.sourceUpdatedAt ?? undefined}
                >{catalogueUpdated}</time
              >
            {:else}
              <span data-unavailable-fields data-availability="unknown"
                >{AVAILABILITY_LABELS.unknown}</span
              >
            {/if}
            <SourcesFreshness
              active={tab === 'overview'}
              systemId={id64}
              sourceUpdatedAt={detail.data.sourceUpdatedAt}
              canonicalGenerationId={detail.data.canonicalGenerationId}
              canonicalGenerationLabel={detail.data.canonicalGenerationLabel}
              ratings={currentRatingsSource}
              stations={ports}
            />
          </dd>
        </div>
        <div
          class="detail-unavailable"
          data-unavailable-fields
          data-availability="not-integrated"
        >
          <dt>Population, economy, security, allegiance, government</dt>
          <dd>{AVAILABILITY_LABELS['not-integrated']}</dd>
        </div>
        <div
          class="detail-unavailable"
          data-unavailable-fields
          data-availability="not-integrated"
        >
          <dt>Development fit</dt>
          <dd>{AVAILABILITY_LABELS['not-integrated']}</dd>
        </div>
      </dl>

      <SystemRatings
        active={tab === 'overview'}
        {id64}
        bodies={detail.data.bodies}
        {headingId}
        onSourceChange={updateRatingsSource}
      />
    </div>
    <div
      bind:this={bodiesPanel}
      id={`${tabId}-panel-bodies`}
      role="tabpanel"
      aria-labelledby={`${tabId}-tab-bodies`}
      tabindex="0"
      hidden={tab !== 'bodies'}
      inert={tab !== 'bodies'}
    >
      {#if detail.data.bodies.length > 0}
        <section class="system-map-panel" aria-labelledby={`${headingId}-map`}>
          <div class="system-map-heading">
            <div>
              <p class="eyebrow">System map</p>
              <h2 id={`${headingId}-map`}>Explore {detail.data.name}</h2>
            </div>
            <span>{integer(detail.data.bodies.length)} catalogue bodies</span>
          </div>
          <p class="system-map-truth-note">
            Laid out as the game's own map: each star heads a row, its planets
            follow it going outward, and each planet's moons hang below it. A
            body whose parent our data does not give is placed by its name or
            its distance, and marked so. Sizes are for reading, not to scale.
            Planet classes come from the catalogue, or from the published
            Ratings V4 derived catalogue where it has none.
          </p>
          {#if mapTree}
            <SystemMap
              tree={mapTree}
              selectedKey={selectedBodyKey ?? null}
              onSelect={selectBody}
            />
          {/if}
          <aside class="system-map-legend" aria-label="System map legend">
            <SystemMapLegend />
            {#if guessedBodies.length > 0}
              <h3>Placed by name or distance</h3>
              <p>Our data does not say what these bodies orbit.</p>
              <ul aria-label="Bodies placed by name or distance">
                {#each guessedBodies as node (node.id)}
                  <li data-guessed-body-key={node.id}>
                    <strong>{node.name}</strong> — {node.guessedParent ===
                    'name'
                      ? 'placed by its name'
                      : 'placed by its distance from the arrival point'}
                  </li>
                {/each}
              </ul>
            {/if}
            {#if mapTree && mapTree.notPlaced.length > 0}
              <h3>Stations not placed at any body</h3>
              <p>
                Our data does not say which body these are at, and none is near
                enough to their distance from the arrival point.
              </p>
              <ul aria-label="Stations not placed at any body">
                {#each mapTree.notPlaced as station (station.id)}
                  <li>{station.name}, {station.kind}</li>
                {/each}
              </ul>
            {/if}
          </aside>
          <div class="system-map-browser">
            <ol aria-label="System bodies">
              {#each detail.data.bodies as body (body.bodyKey)}
                <li>
                  <button
                    type="button"
                    class:active={body.bodyKey === selectedBodyKey}
                    aria-pressed={body.bodyKey === selectedBodyKey}
                    onclick={() => selectBody(body.bodyKey)}
                  >
                    <strong>{body.name}</strong>
                    <span>{bodyClass(body)}</span>
                  </button>
                </li>
              {/each}
            </ol>
            {#if selectedBody}
              <article
                class="system-body-card"
                data-selected-body-id={selectedBody.id}
                data-selected-body-key={selectedBody.bodyKey}
              >
                <p class="eyebrow">Selected body</p>
                <h3>{selectedBody.name}</h3>
                <dl>
                  <div>
                    <dt>Class</dt>
                    <dd>{bodyClass(selectedBody)}</dd>
                  </div>
                  {#if mapTree && selectedNode}
                    {@const orbits = orbitsInWords(mapTree, selectedNode)}
                    {#if orbits}
                      <div>
                        <dt>Orbits</dt>
                        <dd>{orbits}</dd>
                      </div>
                    {/if}
                  {/if}
                  {#if selectedBody.bodyType === 'planet' && selectedBody.bodyClass != null && selectedBody.bodyClassSource === 'ratings-v4-derived'}
                    <div>
                      <dt>Class source</dt>
                      <dd>Ratings V4 derived catalogue</dd>
                    </div>
                  {/if}
                  {#if selectedBody.distanceFromArrivalLs != null}
                    <div>
                      <dt>Arrival distance</dt>
                      <dd>{integer(selectedBody.distanceFromArrivalLs)} ls</dd>
                    </div>
                  {/if}
                  {#if selectedBody.radiusKm != null}
                    <div>
                      <dt>Radius</dt>
                      <dd>{integer(selectedBody.radiusKm)} km</dd>
                    </div>
                  {/if}
                  <div>
                    <dt>Rings</dt>
                    <dd>
                      {selectedRings > 0
                        ? `${selectedRings} known`
                        : 'None recorded'}
                    </dd>
                  </div>
                  {#if selectedBelts > 0}
                    <div>
                      <dt>Belts</dt>
                      <dd>{selectedBelts} known</dd>
                    </div>
                  {/if}
                  {#if selectedBody.isLandable != null}
                    <div>
                      <dt>Landable</dt>
                      <dd>{selectedBody.isLandable ? 'Yes' : 'No'}</dd>
                    </div>
                  {/if}
                  <div>
                    <dt>Signals</dt>
                    <dd>
                      {bodySignalSummary(selectedBody.signals) ??
                        'None recorded'}
                    </dd>
                  </div>
                  {#if selectedNode}
                    {@const slots = groundSlotsInWords(selectedNode)}
                    {#if slots}
                      <div>
                        <dt>Ground slots</dt>
                        <dd>{slots}</dd>
                      </div>
                    {/if}
                    {@const built = builtHere(selectedNode)}
                    {#if built.length > 0}
                      <div class="body-ring-list">
                        <dt>Built here</dt>
                        <dd>
                          {#each built as group (group.where)}
                            <p class="ring-name">{group.where}</p>
                            <ul>
                              {#each group.stations as station (station.id)}
                                <li>{stationInWords(station)}</li>
                              {/each}
                            </ul>
                          {/each}
                        </dd>
                      </div>
                    {/if}
                  {/if}
                  {#if selectedBody.rings.length > 0}
                    <div class="body-ring-list">
                      <dt>Rings, belts and hotspots</dt>
                      <dd>
                        <ul>
                          {#each selectedBody.rings as ring (ring.ringKey)}
                            {@const facts = ringFacts(ring)}
                            <li data-ring-key={ring.ringKey}>
                              <p class="ring-name">{ring.name}</p>
                              {#if facts}
                                <p>{facts}</p>
                              {/if}
                              <p>{ringHotspots(ring)}</p>
                            </li>
                          {/each}
                        </ul>
                      </dd>
                    </div>
                  {/if}
                </dl>
              </article>
            {:else}
              <p class="system-body-empty">
                Select a body in the map or the list for details.
              </p>
            {/if}
          </div>
        </section>
      {:else}
        <p class="state-copy">No bodies are recorded for this system.</p>
      {/if}
    </div>
    <div
      bind:this={stationsPanel}
      id={`${tabId}-panel-stations`}
      role="tabpanel"
      aria-labelledby={`${tabId}-tab-stations`}
      tabindex="0"
      hidden={tab !== 'stations'}
      inert={tab !== 'stations'}
    >
      {#if detail.data.stations.length > 0}
        <section
          class="system-stations"
          aria-labelledby={`${headingId}-stations`}
        >
          <p class="eyebrow">Catalogue</p>
          <h2 id={`${headingId}-stations`}>Stations</h2>
          {#if ports.length > 0}
            <ul>
              {#each ports as station (station.stationKey)}
                {@const economies = stationEconomies(station)}
                <li data-station-key={station.stationKey}>
                  <p class="station-name">{station.name}</p>
                  <p>{stationFacts(station)}</p>
                  <p
                    data-unavailable-fields={economies === null
                      ? ''
                      : undefined}
                    data-availability={economies === null
                      ? 'unknown'
                      : undefined}
                  >
                    {#if economies}
                      {economies}
                    {:else}
                      Economy: <span>{AVAILABILITY_LABELS.unknown}</span>
                    {/if}
                  </p>
                </li>
              {/each}
            </ul>
          {/if}
          {#if carriers.length > 0}
            <details class="station-carriers">
              <summary>
                {carriers.length === 1
                  ? '1 fleet carrier'
                  : `${integer(carriers.length)} fleet carriers`}
              </summary>
              <p class="station-carriers-note">
                Carriers can move at any time; this is where the catalogue last
                saw them.
              </p>
              <ul>
                {#each carriers as station (station.stationKey)}
                  <li data-station-key={station.stationKey}>
                    <p class="station-name">{station.name}</p>
                    <p>{stationFacts(station)}</p>
                  </li>
                {/each}
              </ul>
            </details>
          {/if}
        </section>
      {:else}
        <p class="state-copy">No stations are recorded for this system.</p>
      {/if}
    </div>
  {/if}
</section>

<style>
  [role='tablist'] {
    display: flex;
    gap: 0.4rem;
    padding-bottom: 0.45rem;
    border-bottom: 1px solid var(--line);
  }

  [role='tab'] {
    padding: 0.6rem 0.85rem;
    border: 1px solid transparent;
    border-radius: var(--radius-pill, var(--radius, 0.7rem));
    background: transparent;
    color: var(--text-muted);
    font: inherit;
    font-family: var(--font-hud, 'Inter', 'Segoe UI', sans-serif);
    cursor: pointer;
  }

  [role='tab'][aria-selected='true'] {
    color: inherit;
    box-shadow: inset 0 -2px var(--accent);
  }

  [role='tab']:hover {
    color: inherit;
  }

  [role='tab']:not([aria-selected='true']):hover {
    box-shadow: inset 0 -2px var(--line-strong);
  }

  [role='tab']:focus-visible,
  [role='tabpanel']:focus-visible {
    outline: 2px solid var(--neon);
    outline-offset: 2px;
  }

  [role='tabpanel'] {
    height: min(70dvh, 54rem);
    min-height: 20rem;
    overflow: auto;
    scrollbar-gutter: stable;
    background: var(--panel);
    border-radius: var(--radius, 0.7rem);
    padding: 0.75rem;
  }

  [role='tabpanel'][hidden] {
    display: none;
  }

  .detail-grid {
    margin-top: 0;
  }

  .system-map-legend {
    padding: 0.75rem 1rem;
    color: var(--text-muted);
    font-size: 0.78rem;
    line-height: 1.55;
  }

  .system-map-legend p {
    margin: 0;
  }

  .system-map-legend h3 {
    margin: 0.75rem 0 0.25rem;
    color: var(--text);
    font-size: 0.85rem;
  }

  .system-map-legend ul {
    max-height: 12rem;
    overflow: auto;
    margin: 0.5rem 0 0;
    padding-left: 1.25rem;
  }
</style>
