<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  import {
    isTopmostPopout,
    registerPopout,
  } from '$lib/components/popout-stack';

  let {
    densityEnabled,
    journeysEnabled,
    nebulaeEnabled,
    onDensityToggle,
    onJourneysToggle,
    onNebulaeToggle,
    densityCellSizeLy,
    densityGeneration,
    densityCoverage = null,
    journeysMaxVisits,
    densityStatus,
    journeysStatus,
    nebulaeStatus,
  }: {
    densityEnabled: boolean;
    journeysEnabled: boolean;
    nebulaeEnabled: boolean;
    onDensityToggle: () => void;
    onJourneysToggle: () => void;
    onNebulaeToggle: () => void;
    densityCellSizeLy: number | null;
    densityGeneration: string | null;
    densityCoverage?: string | null;
    journeysMaxVisits?: number;
    densityStatus: Snippet;
    journeysStatus: Snippet;
    nebulaeStatus: Snippet;
  } = $props();

  const id = $props.id();
  const dialogId = `${id}-map-layers`;
  let open = $state(false);
  let trigger = $state<HTMLButtonElement>();
  let panel = $state<HTMLDivElement>();
  let layer = $state(30);
  let panelLeft = $state(8);
  let panelTop = $state(8);
  let panelMaxWidth = $state<number | null>(null);
  let panelMaxHeight = $state<number | null>(null);
  const enabledCount = $derived(
    Number(densityEnabled) + Number(journeysEnabled) + Number(nebulaeEnabled),
  );

  function positionPanel() {
    if (!panel || !trigger) return;
    const anchor = trigger.getBoundingClientRect();
    const bounds = panel.getBoundingClientRect();
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
    // Both dimensions follow the visible viewport, including pinch zoom and
    // mobile keyboards. ResizeObserver positions the panel after it resizes.
    panelMaxWidth = Math.max(0, Math.floor(width - margin * 2));
    panelMaxHeight = Math.max(0, Math.floor(height - margin * 2));
    const panelWidth = Math.min(bounds.width, panelMaxWidth);
    const panelHeight = Math.min(bounds.height, panelMaxHeight);
    const maxLeft = Math.max(left + margin, left + width - panelWidth - margin);
    const maxTop = Math.max(top + margin, top + height - panelHeight - margin);
    const beside = anchor.right + margin;
    panelLeft = Math.max(
      left + margin,
      Math.min(
        beside <= maxLeft ? beside : anchor.left - panelWidth - margin,
        maxLeft,
      ),
    );
    panelTop = Math.max(top + margin, Math.min(anchor.top, maxTop));
  }

  function closePanel() {
    open = false;
    trigger?.focus({ preventScroll: true });
  }

  async function togglePanel() {
    if (open) {
      closePanel();
      return;
    }
    open = true;
    await tick();
    if (!open) return;
    positionPanel();
    panel
      ?.querySelector<HTMLButtonElement>('[role="switch"]')
      ?.focus({ preventScroll: true });
  }

  function keydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape' && isTopmostPopout(panel)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      closePanel();
    }
  }

  function outsideClick(event: MouseEvent) {
    if (
      open &&
      isTopmostPopout(panel) &&
      event.target instanceof Node &&
      !panel?.contains(event.target) &&
      !trigger?.contains(event.target)
    )
      closePanel();
  }

  $effect(() => {
    const element = panel;
    if (!element) return;
    const unregister = registerPopout(element, (value) => (layer = value));
    window.addEventListener('keydown', keydown, true);
    return () => {
      unregister();
      window.removeEventListener('keydown', keydown, true);
    };
  });

  $effect(() => {
    const element = panel;
    if (!element) return;
    positionPanel();
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(positionPanel);
    observer?.observe(element);
    const scroll = (event: Event) => {
      if (event.target instanceof Node && element.contains(event.target))
        return;
      positionPanel();
    };
    const viewport = window.visualViewport;
    window.addEventListener('scroll', scroll, true);
    window.addEventListener('resize', positionPanel);
    viewport?.addEventListener('resize', positionPanel);
    viewport?.addEventListener('scroll', positionPanel);
    return () => {
      observer?.disconnect();
      window.removeEventListener('scroll', scroll, true);
      window.removeEventListener('resize', positionPanel);
      viewport?.removeEventListener('resize', positionPanel);
      viewport?.removeEventListener('scroll', positionPanel);
    };
  });
</script>

<svelte:window onclick={outsideClick} />

<button
  bind:this={trigger}
  type="button"
  class="layers-trigger"
  data-map-layers-toggle
  aria-haspopup="dialog"
  aria-expanded={open}
  aria-controls={open ? dialogId : undefined}
  onclick={togglePanel}
>
  Layers · {enabledCount}
</button>

{#if open}
  <div
    bind:this={panel}
    id={dialogId}
    class="layers-dialog"
    data-map-layers-dialog
    role="dialog"
    aria-modal="false"
    aria-labelledby={`${dialogId}-title`}
    tabindex="-1"
    style:left={`${panelLeft}px`}
    style:z-index={layer}
    style:top={`${panelTop}px`}
    style:max-width={panelMaxWidth === null ? undefined : `${panelMaxWidth}px`}
    style:max-height={panelMaxHeight === null
      ? undefined
      : `${panelMaxHeight}px`}
  >
    <div class="layers-heading">
      <h2 id={`${dialogId}-title`}>Map layers</h2>
      <button
        type="button"
        class="close-button"
        aria-label="Close map layers"
        onclick={closePanel}
      >
        Close
      </button>
    </div>

    <section class="layer-row" aria-labelledby={`${id}-density-label`}>
      <button
        type="button"
        class="layer-switch"
        role="switch"
        aria-checked={densityEnabled}
        aria-labelledby={`${id}-density-label`}
        aria-describedby={`${id}-density-description`}
        data-density-toggle
        onclick={() => onDensityToggle()}
      >
        <span id={`${id}-density-label`}>Known-system density</span>
        <span class="switch-track" aria-hidden="true"><span></span></span>
      </button>
      <p id={`${id}-density-description`}>
        Public aggregate of catalogue systems per cell in this view; detail
        changes with zoom.
      </p>
      <div class="layer-legend" aria-label="Known-system density legend">
        <div class="colour-ramp density-ramp" aria-hidden="true"></div>
        <div class="legend-ends">
          <span>fewer systems</span><span>more systems</span>
        </div>
        <p>
          {#if densityCoverage}
            {densityCoverage}
          {:else if densityCellSizeLy !== null}
            {densityCellSizeLy.toLocaleString()} LY cells
          {:else}
            Cell size available when loaded
          {/if}{#if densityGeneration}
            · Generation: {densityGeneration}{/if}
        </p>
      </div>
      <div class="layer-status">{@render densityStatus()}</div>
    </section>

    <section class="layer-row" aria-labelledby={`${id}-journeys-label`}>
      <button
        type="button"
        class="layer-switch"
        role="switch"
        aria-checked={journeysEnabled}
        aria-labelledby={`${id}-journeys-label`}
        aria-describedby={`${id}-journeys-description`}
        data-commander-history-toggle
        onclick={() => onJourneysToggle()}
      >
        <span id={`${id}-journeys-label`}>My journeys</span>
        <span class="switch-track" aria-hidden="true"><span></span></span>
      </button>
      <p id={`${id}-journeys-description`}>
        Your imported journal travel, private to your account.
      </p>
      {#if journeysMaxVisits !== undefined}
        <div class="layer-legend" aria-label="My journeys visit-count legend">
          <div class="colour-ramp journeys-ramp" aria-hidden="true"></div>
          <div class="legend-ends">
            <span>1 visit</span>
            <span
              >{journeysMaxVisits.toLocaleString()}
              {journeysMaxVisits === 1 ? 'visit' : 'visits'}</span
            >
          </div>
        </div>
      {/if}
      <div class="layer-status">{@render journeysStatus()}</div>
    </section>

    <section class="layer-row" aria-labelledby={`${id}-nebulae-label`}>
      <button
        type="button"
        class="layer-switch"
        role="switch"
        aria-checked={nebulaeEnabled}
        aria-labelledby={`${id}-nebulae-label`}
        aria-describedby={`${id}-nebulae-description`}
        data-nebula-toggle
        onclick={() => onNebulaeToggle()}
      >
        <span id={`${id}-nebulae-label`}>Nebulae</span>
        <span class="switch-track" aria-hidden="true"><span></span></span>
      </button>
      <p id={`${id}-nebulae-description`}>
        Catalogue nebula landmarks, shown at the appropriate zoom.
      </p>
      <div class="nebula-legend layer-legend" aria-label="Nebulae legend">
        <span
          ><i class="diffuse-marker" aria-hidden="true"></i>Diffuse clouds</span
        >
        <span
          ><i class="planetary-marker" aria-hidden="true"></i>Planetary markers</span
        >
      </div>
      <div class="layer-status">{@render nebulaeStatus()}</div>
    </section>
  </div>
{/if}

<style>
  .layers-trigger,
  .close-button {
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel);
    background-image: var(--panel-gloss);
    color: inherit;
    cursor: pointer;
  }
  .layers-trigger[aria-expanded='true'] {
    border-color: var(--neon);
  }
  .layers-dialog {
    position: fixed;
    box-sizing: border-box;
    width: min(27rem, calc(100vw - 1rem));
    max-height: calc(100dvh - 1rem);
    overflow: auto;
    overscroll-behavior: contain;
    border: 1px solid var(--neon);
    border-radius: var(--radius-lg);
    background: var(--panel);
    background-image: var(--panel-gloss);
    color: var(--text);
    box-shadow: var(--line-glow), var(--shadow-panel);
    font-size: 0.8rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .layers-heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    padding: 0.7rem 1rem;
  }
  h2 {
    margin: 0;
    font-size: 1rem;
  }
  .layer-row {
    padding: 0.85rem 1rem;
    border-top: 1px solid var(--line);
  }
  .layer-switch {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    width: 100%;
    min-height: 2rem;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: left;
    font-weight: 650;
    cursor: pointer;
  }
  .switch-track {
    display: flex;
    flex: 0 0 2rem;
    align-items: center;
    height: 1.15rem;
    padding: 2px;
    border: 1px solid var(--muted);
    border-radius: 1rem;
    background: var(--panel-raised);
  }
  .switch-track span {
    width: 0.75rem;
    height: 0.75rem;
    border-radius: 50%;
    background: var(--muted);
  }
  [aria-checked='true'] .switch-track {
    justify-content: flex-end;
    border-color: var(--color-cyan);
    background: #12354a;
  }
  [aria-checked='true'] .switch-track span {
    background: var(--color-cyan);
  }
  p {
    margin: 0.35rem 0 0;
  }
  .layer-row > p,
  .layer-legend,
  .layer-status {
    color: var(--muted);
    font-size: 0.75rem;
  }
  .layer-legend,
  .layer-status {
    margin-top: 0.65rem;
  }
  .colour-ramp {
    height: 0.4rem;
    border-radius: 0.25rem;
  }
  /* Match catalogueDensityHeatColour and commanderHistoryHeatColour without
     importing the Babylon renderer into accessible application controls. */
  .density-ramp {
    background: linear-gradient(90deg, #33578c, #9e8094 56%, #ffd6a3);
  }
  .journeys-ramp {
    background: linear-gradient(90deg, #1447ff, #14dbd1 50%, #ffdb24);
  }
  .legend-ends {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    margin-top: 0.2rem;
  }
  .nebula-legend,
  .nebula-legend > span {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem 0.85rem;
  }
  .nebula-legend > span {
    gap: 0.35rem;
  }
  .diffuse-marker {
    width: 1rem;
    height: 0.65rem;
    border-radius: 50%;
    background: radial-gradient(ellipse, #db5785, #9466d1 60%, transparent);
  }
  .planetary-marker {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 50%;
    background: #4d808c;
  }
  .layer-status :global(button) {
    min-height: 2rem;
    margin-left: 0.4rem;
    padding: 0.25rem 0.5rem;
    border: 1px solid var(--line);
    border-radius: 0.3rem;
    background: var(--panel-raised);
    color: #e4edf4;
    cursor: pointer;
  }
</style>
