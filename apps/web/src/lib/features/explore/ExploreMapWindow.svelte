<script lang="ts">
  import type { Snippet } from 'svelte';

  // The Explore map fills the window beside the Finder panel, taking turns
  // there with Find's results. It is part of the page, not a pop-out: there
  // is nothing to open, close or expand. This is the frame only; the page
  // puts the map itself inside it.
  let {
    finderPanelOpen,
    onShowFinder,
    inFront = true,
    onShowResults,
    id = 'explore-map-window',
    element = $bindable(),
    beforeCanvas,
    children,
    afterCanvas,
    selectedSystemName = null,
  }: {
    finderPanelOpen: boolean;
    onShowFinder: () => void;
    /** Whether the map can be seen. Behind the results (or the Finder at
     *  phone width) it keeps its size but takes no focus. */
    inFront?: boolean;
    /** Given when Find's results are behind the map. */
    onShowResults?: () => void;
    id?: string;
    element?: HTMLElement;
    beforeCanvas?: Snippet;
    /** The map. */
    children?: Snippet;
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
  data-map-in-front={inFront}
  inert={!inFront}
>
  <header class="map-window-heading">
    <!-- The map draws its own title bar, so this heading is for screen
         readers: it names the window, and the system it shows. -->
    <h2 id={`${id}-title`} class="map-window-title">
      {selectedSystemName ? `${selectedSystemName} · Galaxy map` : 'Galaxy map'}
    </h2>
    {#if !finderPanelOpen && inFront}
      <!-- In the row above the map: the map's own panels fill its corners. -->
      <button
        type="button"
        class="quiet-button finder-show"
        data-finder-panel-show
        aria-controls="finder-panel"
        aria-expanded="false"
        onclick={onShowFinder}>Show Finder</button
      >
    {/if}
    {#if onShowResults && inFront}
      <button
        type="button"
        class="quiet-button"
        data-results-show
        aria-controls="explore-results"
        onclick={onShowResults}>Show results</button
      >
    {/if}
    {@render beforeCanvas?.()}
  </header>
  <div class="map-window-content">
    {@render children?.()}
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
  .map-window-title {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  /* The page's own controls for the map fill the row; the row's own line
     under them is the only one. */
  .map-window-heading :global(.map-layer-toolbar) {
    flex: 1 1 20rem;
    min-width: 0;
    padding: 0;
    border-bottom: 0;
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
  /* The same size as the map's other controls in this row. */
  .quiet-button {
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
    border: 1px solid var(--accent);
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    color: var(--text);
    font-size: 0.75rem;
    cursor: pointer;
  }
  .quiet-button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  @media (max-width: 700px) {
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
