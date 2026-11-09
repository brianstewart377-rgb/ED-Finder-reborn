<script lang="ts">
  import type { ComponentProps, Snippet } from 'svelte';
  import SpatialCanvas from '$lib/spatial/SpatialCanvas.svelte';

  // The Explore map fills the window beside the Finder panel. It is part of
  // the page, not a pop-out: there is nothing to open, close or expand.
  let {
    canvasProps,
    finderPanelOpen,
    onShowFinder,
    hoveredSystemId = null,
    id = 'explore-map-window',
    element = $bindable(),
    beforeCanvas,
    afterCanvas,
    selectedSystemName = null,
  }: {
    canvasProps: ComponentProps<typeof SpatialCanvas>;
    finderPanelOpen: boolean;
    onShowFinder: () => void;
    hoveredSystemId?: string | null;
    id?: string;
    element?: HTMLElement;
    beforeCanvas?: Snippet;
    afterCanvas?: Snippet;
    selectedSystemName?: string | null;
  } = $props();
</script>

<section
  bind:this={element}
  {id}
  class="map-panel explore-map-window"
  aria-labelledby={`${id}-title`}
  data-explore-map-window
  data-finder-panel-open={finderPanelOpen}
  data-hovered-system-id={hoveredSystemId ?? ''}
>
  <header class="map-window-heading">
    <h2 id={`${id}-title`}>
      {selectedSystemName ? `${selectedSystemName} · Galaxy map` : 'Galaxy map'}
    </h2>
    {@render beforeCanvas?.()}
  </header>
  <div class="map-window-content">
    {#if !finderPanelOpen}
      <!-- Floats over the map so showing it never changes the canvas size. -->
      <button
        type="button"
        class="quiet-button finder-show"
        data-finder-panel-show
        aria-controls="finder-panel"
        aria-expanded="false"
        onclick={onShowFinder}>Show Finder</button
      >
    {/if}
    <SpatialCanvas {...canvasProps} controlsOverlay />
  </div>
  <footer class="map-window-footer">
    {@render afterCanvas?.()}
  </footer>
</section>

<style>
  .explore-map-window {
    position: relative;
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    height: 100%;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-lg);
    background: var(--panel);
    background-image: var(--panel-gloss);
    box-shadow: var(--shadow-panel);
    color: var(--text);
  }
  .map-window-heading {
    display: flex;
    flex-shrink: 0;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem 0.75rem;
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--line);
  }
  .map-window-heading h2 {
    margin: 0;
    font-family: var(--font-hud);
    font-size: 1.05rem;
    min-width: 0;
    max-width: 22rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .map-window-heading :global(.map-layer-toolbar) {
    padding: 0;
  }
  .map-window-content {
    position: relative;
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
    padding: 0.4rem 0.5rem 0;
    gap: 0.25rem;
  }
  .map-window-content :global(.map-systems-surface) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .map-window-content :global(.spatial-canvas) {
    flex: 1;
    min-height: 0;
    height: auto;
    border: 1px solid var(--line);
  }
  .map-window-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    flex-shrink: 0;
    gap: 0.35rem 0.6rem;
    margin-top: 0.25rem;
    padding: 0.35rem 0.75rem;
    border-top: 1px solid var(--line);
    background-image: var(--panel-gloss);
  }
  .map-window-footer:empty {
    display: none;
  }
  .quiet-button {
    min-height: 2.25rem;
    padding: 0.35rem 0.75rem;
    border: 1px solid var(--accent);
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    color: var(--text);
    cursor: pointer;
  }
  .finder-show {
    position: absolute;
    z-index: 5;
    top: 0.85rem;
    left: 1rem;
    box-shadow: var(--shadow-panel);
  }
  .quiet-button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  @media (max-width: 700px) {
    .map-window-heading h2 {
      font-size: 0.95rem;
    }
    .map-window-heading,
    .map-window-footer {
      padding: 0.35rem 0.5rem;
      gap: 0.3rem;
    }
    .map-window-content {
      padding: 0.3rem 0.35rem 0;
    }
    .quiet-button {
      font-size: 0.75rem;
    }
  }
</style>
