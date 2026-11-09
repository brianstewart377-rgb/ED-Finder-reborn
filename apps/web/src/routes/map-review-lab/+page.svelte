<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';

  import SpatialCanvas from '$lib/spatial/SpatialCanvas.svelte';
  import type {
    CameraState,
    GalaxySceneContract,
    RuntimeEvent,
    SpatialContribution,
  } from '$lib/spatial/contracts';
  import { createCatalogueDensityContribution } from '$lib/spatial/galaxy-density';
  import {
    fitGalaxyPlaneCamera,
    focusGalaxyCamera,
  } from '$lib/spatial/galaxy-camera';
  import {
    buildGalaxyReviewFixtureScene,
    galaxyReviewDensity,
    galaxyReviewDensityRefreshed,
    galaxyReviewSystems,
  } from '$lib/spatial/galaxy-review-fixture';
  import {
    createGalaxyRegionsContribution,
    fetchAuthoritativeGalaxyRegions,
    type GalaxyRegionsPayload,
  } from '$lib/spatial/galaxy-regions';

  const baseScene = buildGalaxyReviewFixtureScene();
  let scene = $state(baseScene);
  let activeDensity = $state(galaxyReviewDensity);
  let contributionPatch = $state<SpatialContribution | null>(null);
  let contributionPatchRevision = $state(0);
  let regions = $state<GalaxyRegionsPayload | null>(null);
  let regionState = $state<'loading' | 'ready' | 'failed'>('loading');
  let selectedRegionId = $state<string | null>(null);
  let hoveredRegionId = $state<string | null>(null);
  let cameraRequest = $state<CameraState | null>(null);
  let cameraRequestRevision = $state(0);
  let focusTarget = $state<{ kind: 'system'; systemId64: string } | null>(null);
  let focusRevision = $state(0);
  let mapSection: HTMLElement;

  function requestView(camera: CameraState): void {
    cameraRequest = camera;
    cameraRequestRevision += 1;
  }

  function overview(): void {
    if (!regions) return;
    const { origin, pixel_scale } = regions.lookup;
    const canvas = mapSection.querySelector('canvas');
    requestView(
      fitGalaxyPlaneCamera(
        { ...scene.camera, bearingRad: 0, pitchRad: Math.PI / 2 },
        {
          min: { x: origin.x, y: 0, z: origin.z },
          max: {
            x: origin.x + regions.source.width * pixel_scale,
            y: 0,
            z: origin.z + regions.source.height * pixel_scale,
          },
        },
        {
          width: canvas?.clientWidth || 1000,
          height: canvas?.clientHeight || 500,
        },
      ),
    );
  }

  function viewSelectedRegion(): void {
    const region = regions?.regions.find(
      (candidate) => String(candidate.id) === selectedRegionId,
    );
    if (region)
      requestView(focusGalaxyCamera(scene.camera, region.labelLy, 18_000));
  }

  function composeRegionScene(
    accepted: GalaxyRegionsPayload,
    revision: number,
  ): GalaxySceneContract {
    return {
      ...baseScene,
      revision,
      camera: { ...baseScene.camera, revision },
      selection: selectedRegionId
        ? [
            ...baseScene.selection,
            { kind: 'region', id: selectedRegionId } as const,
          ]
        : baseScene.selection,
      contributions: [
        ...baseScene.contributions,
        createGalaxyRegionsContribution(accepted, revision),
      ],
    };
  }

  function regionName(regionId: string | null): string {
    if (!regionId || !regions) return 'None';
    return (
      regions.regions.find((region) => String(region.id) === regionId)?.name ??
      'Unknown'
    );
  }

  function selectRegion(regionId: string | null): void {
    if (regionId === selectedRegionId || !regions) return;
    selectedRegionId = regionId;
    scene = {
      ...scene,
      revision: scene.revision + 1,
      selection: regionId
        ? [...baseScene.selection, { kind: 'region', id: regionId } as const]
        : baseScene.selection,
    };
  }

  function simulateCatalogueRefresh(): void {
    if (
      activeDensity.generationId === galaxyReviewDensityRefreshed.generationId
    )
      return;
    const replacement = createCatalogueDensityContribution(
      galaxyReviewDensityRefreshed,
      2,
    );
    activeDensity = galaxyReviewDensityRefreshed;
    // Keep the accepted scene contract coherent for any later scene reload,
    // while PATCH_CONTRIBUTION updates only the live density GPU resources now.
    scene = {
      ...scene,
      contributions: scene.contributions.map((contribution) =>
        contribution.id === replacement.id ? replacement : contribution,
      ),
    };
    contributionPatch = replacement;
    contributionPatchRevision += 1;
  }

  function handleRuntimeEvent(event: RuntimeEvent): void {
    if (event.type === 'CAMERA_CHANGED' && 'focusLy' in event.camera) {
      scene = { ...scene, camera: event.camera };
    } else if (event.type === 'TARGET_HOVERED') {
      hoveredRegionId =
        event.target?.kind === 'region' ? event.target.id : null;
    } else if (
      event.type === 'TARGET_PICKED' &&
      event.target?.kind === 'region'
    ) {
      selectRegion(event.target.id);
    }
  }

  async function loadRegions(): Promise<void> {
    regionState = 'loading';
    try {
      const accepted = await fetchAuthoritativeGalaxyRegions();
      const revision = baseScene.revision + 1;
      regions = accepted;
      scene = composeRegionScene(accepted, revision);
      regionState = 'ready';
    } catch {
      regions = null;
      scene = baseScene;
      regionState = 'failed';
    }
  }

  onMount(() => {
    const requestedRegion = new URL(window.location.href).searchParams.get(
      'region',
    );
    const requestedRegionNumber = Number(requestedRegion);
    if (
      requestedRegion &&
      Number.isSafeInteger(requestedRegionNumber) &&
      requestedRegionNumber >= 1 &&
      requestedRegionNumber <= 42
    ) {
      selectedRegionId = requestedRegion;
    }
    void loadRegions();
  });
</script>

<svelte:head>
  <title>Map Review Lab — ED-Finder V3</title>
  <meta
    name="description"
    content="Deterministic non-product Galaxy Map review surface"
  />
</svelte:head>

<main class="map-review-lab">
  <a class="back-link" href={resolve('/explore')}>← Explore</a>
  <header>
    <p class="eyebrow">NON-PRODUCT · deterministic fixture</p>
    <h1>Galaxy Map Review Lab</h1>
    <p>
      This surface exercises the real Babylon product renderer with traceable
      fixture coordinates. It is not production catalogue coverage and contains
      no generated replacement for missing density.
    </p>
  </header>

  <section class="truth-panel" aria-labelledby="truth-title">
    <div>
      <p class="eyebrow">Accepted layer</p>
      <h2 id="truth-title">Known systems density</h2>
    </div>
    <dl>
      <div>
        <dt>Generation</dt>
        <dd data-density-generation={activeDensity.generationId}>
          <code>{activeDensity.generationId}</code>
        </dd>
      </div>
      <div>
        <dt>Source systems</dt>
        <dd data-density-source-count={activeDensity.sourceSystemCount}>
          {activeDensity.sourceSystemCount}
        </dd>
      </div>
      <div>
        <dt>Reconciled systems</dt>
        <dd data-density-covered-count={activeDensity.coveredSystemCount}>
          {activeDensity.coveredSystemCount}
        </dd>
      </div>
      <div>
        <dt>Occupied cells</dt>
        <dd data-density-cell-count={activeDensity.cells.length}>
          {activeDensity.cells.length}
        </dd>
      </div>
      <div>
        <dt>Coverage</dt>
        <dd>{activeDensity.complete ? 'Complete fixture' : 'Partial'}</dd>
      </div>
    </dl>
    <div class="fixture-refresh">
      <p>
        Prove that a newer accepted catalogue generation replaces density
        without resetting the current camera, regions, or selection.
      </p>
      <button
        type="button"
        disabled={activeDensity.generationId ===
          galaxyReviewDensityRefreshed.generationId}
        onclick={simulateCatalogueRefresh}
      >
        Simulate 6 new discoveries
      </button>
    </div>
  </section>

  <section class="region-panel" aria-labelledby="region-title">
    <div>
      <p class="eyebrow">Pinned in-game geography</p>
      <h2 id="region-title">All 42 Galactic regions</h2>
      <p>
        Geometry and lookup are derived at build time from the audited 2048² RLE
        source. Hover illuminates the complete exact source-cell footprint;
        selection persists it in amber, and all 42 source-derived names remain
        visible in the overview. Unknown space remains unknown.
      </p>
    </div>
    {#if regionState === 'loading'}
      <p role="status">Loading the authoritative region resource…</p>
    {:else if regionState === 'failed'}
      <div role="alert" class="region-error">
        <p>The authoritative region resource failed validation.</p>
        <button type="button" onclick={loadRegions}>Retry regions</button>
      </div>
    {:else if regions}
      <dl class="region-receipt">
        <div>
          <dt>Regions</dt>
          <dd data-region-count={regions.regions.length}>
            {regions.regions.length}/42
          </dd>
        </div>
        <div>
          <dt>Boundaries</dt>
          <dd data-region-boundary-count={regions.boundaries.length}>
            {regions.boundaries.length.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>Source hash</dt>
          <dd><code>{regions.source.sha256.slice(0, 12)}…</code></dd>
        </div>
        <div>
          <dt>Rights</dt>
          <dd>Non-commercial only</dd>
        </div>
        <div>
          <dt>Interaction</dt>
          <dd>Exact fill lookup</dd>
        </div>
      </dl>
      <label class="region-picker" for="region-picker">
        <span>Keyboard region selection</span>
        <select
          id="region-picker"
          value={selectedRegionId ?? ''}
          onchange={(event) => selectRegion(event.currentTarget.value || null)}
        >
          <option value="">No region selected</option>
          {#each regions.regions as region (region.id)}
            <option value={String(region.id)}>
              {region.id}. {region.name}
            </option>
          {/each}
        </select>
      </label>
      <details>
        <summary>Review the exact 42 names</summary>
        <ol class="region-list">
          {#each regions.regions as region (region.id)}
            <li><span>{region.id}</span> {region.name}</li>
          {/each}
        </ol>
      </details>
      <p class="attribution">{regions.source.attribution}</p>
    {/if}
  </section>

  <section
    class="map-panel"
    aria-labelledby="fixture-map-title"
    bind:this={mapSection}
  >
    <div class="map-heading">
      <div>
        <p class="eyebrow">Babylon render product</p>
        <h2 id="fixture-map-title">Explore the Galaxy</h2>
      </div>
      <span>Ambient layer: off</span>
    </div>
    <div class="region-interaction" aria-live="polite">
      <span>
        Selected:
        <strong data-selected-region-id={selectedRegionId ?? ''}>
          {regionName(selectedRegionId)}
        </strong>
      </span>
      <span>
        Hovered:
        <strong data-hovered-region-id={hoveredRegionId ?? ''}>
          {regionName(hoveredRegionId)}
        </strong>
      </span>
    </div>
    <div class="view-presets" role="group" aria-label="Map views">
      <button type="button" disabled={!regions} onclick={overview}
        >All 42 regions</button
      >
      <button
        type="button"
        onclick={() =>
          requestView({
            ...baseScene.camera,
            revision: scene.camera.revision + 1,
          })}>Fixture neighbourhood</button
      >
      <button
        type="button"
        disabled={!selectedRegionId}
        onclick={viewSelectedRegion}>View selected region</button
      >
    </div>
    <SpatialCanvas
      {scene}
      {cameraRequest}
      {cameraRequestRevision}
      {contributionPatch}
      {contributionPatchRevision}
      {focusTarget}
      {focusRevision}
      onRuntimeEvent={handleRuntimeEvent}
    />
  </section>

  <section class="target-panel" aria-labelledby="target-title">
    <p class="eyebrow">Semantic mirror</p>
    <h2 id="target-title">Fixture system targets</h2>
    <ul>
      {#each galaxyReviewSystems as system (system.systemId64)}
        <li>
          <strong>{system.name}</strong>
          <code>{system.systemId64}</code>
          <span>
            {system.positionLy.x}, {system.positionLy.y}, {system.positionLy.z}
            ly
          </span>
          <button
            type="button"
            onclick={() => {
              focusTarget = { kind: 'system', systemId64: system.systemId64 };
              focusRevision += 1;
              mapSection.scrollIntoView({ block: 'center' });
            }}>Focus system</button
          >
        </li>
      {/each}
    </ul>
  </section>
</main>

<style>
  .map-review-lab {
    display: grid;
    min-width: 0;
    gap: 1.5rem;
    padding: 2rem 0 4rem;
  }

  .back-link {
    width: fit-content;
  }

  header {
    max-width: 58rem;
  }

  header p:last-child {
    max-width: 52rem;
    color: #b6c3d0;
    line-height: 1.65;
  }

  .truth-panel,
  .region-panel,
  .map-panel,
  .target-panel {
    min-width: 0;
    border: 1px solid #2e4053;
    background: linear-gradient(145deg, #0b131d, #070b11);
    box-shadow: 0 1.2rem 3rem rgb(0 0 0 / 22%);
  }

  .truth-panel,
  .region-panel,
  .target-panel {
    padding: 1.25rem;
  }

  .truth-panel {
    display: grid;
    grid-template-columns: minmax(12rem, 0.6fr) minmax(20rem, 1.4fr);
    gap: 1.5rem;
    align-items: start;
  }

  .truth-panel dl {
    grid-column: 2;
    grid-row: 1 / span 2;
  }

  .fixture-refresh {
    display: grid;
    grid-column: 1;
    grid-row: 2;
    gap: 0.65rem;
    align-content: start;
  }

  .fixture-refresh p {
    margin: 0;
    color: #9fb0c0;
    line-height: 1.45;
  }

  .fixture-refresh button {
    min-height: 2.75rem;
    border: 1px solid #436b82;
    border-radius: 0.4rem;
    background: #102a3a;
    color: #dcf5ff;
    cursor: pointer;
  }

  .fixture-refresh button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .region-panel {
    display: grid;
    gap: 1rem;
  }

  .region-panel > div:first-child p:last-child,
  .attribution {
    color: #9fb0c0;
    line-height: 1.55;
  }

  .region-receipt {
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  }

  .region-error {
    display: flex;
    gap: 1rem;
    align-items: center;
  }

  .region-error p {
    margin: 0;
  }

  .region-error button {
    min-height: 2.75rem;
  }

  .region-picker {
    display: grid;
    grid-template-columns: minmax(12rem, 0.4fr) minmax(16rem, 1fr);
    gap: 0.75rem;
    align-items: center;
    color: #b9dff5;
    font-weight: 700;
  }

  .region-picker select {
    width: 100%;
    min-height: 2.75rem;
    border: 1px solid #39536a;
    border-radius: 0.3rem;
    padding: 0.55rem 0.7rem;
    background: #08101a;
    color: #e8f3fc;
    font: inherit;
  }

  details {
    border-top: 1px solid #26394b;
    padding-top: 0.9rem;
  }

  summary {
    cursor: pointer;
    color: #b9dff5;
    font-weight: 700;
  }

  .region-list {
    columns: 3 14rem;
    gap: 1.5rem;
    margin: 1rem 0 0;
    padding-left: 1.8rem;
  }

  .region-list li {
    display: list-item;
    break-inside: avoid;
    margin-bottom: 0.35rem;
    padding: 0;
    border: 0;
    background: transparent;
  }

  .region-list span {
    display: inline-block;
    min-width: 2rem;
    color: #78b9df;
  }

  .attribution {
    margin: 0;
    font-size: 0.78rem;
  }

  h1,
  h2,
  p {
    margin-top: 0;
  }

  dl {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: 0.8rem;
    margin: 0;
  }

  dl div {
    min-width: 0;
    padding: 0.75rem;
    border: 1px solid #24384a;
    background: #08101a;
  }

  dt {
    color: #8fa4b8;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  dd {
    margin: 0.35rem 0 0;
    color: #e8f3fc;
    font-weight: 700;
  }

  dd code {
    overflow-wrap: anywhere;
  }

  .map-panel {
    padding: 1rem;
    border-radius: 0.8rem;
  }

  .view-presets {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.85rem;
  }
  .view-presets button,
  li button {
    border: 1px solid #36556b;
    border-radius: 0.4rem;
    background: #112536;
    color: #d8ecf6;
    padding: 0.5rem 0.85rem;
    cursor: pointer;
  }
  .view-presets button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .view-presets button:hover:enabled,
  li button:hover {
    background: #1d3c50;
  }

  .map-heading {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: start;
    padding: 0.25rem 0.25rem 0.8rem;
  }

  .map-heading span {
    flex-shrink: 0;
    color: #8ec8ee;
    font-size: 0.82rem;
  }

  .region-interaction {
    display: flex;
    flex-wrap: wrap;
    gap: 0.65rem 1.5rem;
    padding: 0.65rem 0.25rem 0.8rem;
    color: #91a6b9;
    font-size: 0.85rem;
  }

  .region-interaction strong {
    margin-left: 0.35rem;
    color: #e8f3fc;
  }

  ul {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 0.75rem;
    padding: 0;
    margin: 0;
    list-style: none;
  }

  li {
    display: grid;
    gap: 0.25rem;
    padding: 0.8rem;
    border: 1px solid #26394b;
    background: #08101a;
  }

  li code,
  li span {
    color: #9eb0c1;
    font-size: 0.82rem;
  }

  @media (max-width: 760px) {
    .truth-panel {
      grid-template-columns: 1fr;
    }

    .truth-panel dl,
    .fixture-refresh {
      grid-column: 1;
      grid-row: auto;
    }

    .map-heading {
      display: grid;
    }

    .region-picker {
      grid-template-columns: 1fr;
    }
  }
</style>
