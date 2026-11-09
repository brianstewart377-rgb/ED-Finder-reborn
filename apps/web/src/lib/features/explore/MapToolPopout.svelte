<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  import {
    isTopmostPopout,
    registerPopout,
  } from '$lib/components/popout-stack';

  let {
    kind,
    label,
    title,
    children,
  }: {
    kind: 'regions' | 'sources' | 'selection';
    label: string;
    title: string;
    children: Snippet;
  } = $props();

  const id = $props.id();
  const dialogId = $derived(`${id}-map-${kind}`);
  let open = $state(false);
  let trigger = $state<HTMLButtonElement>();
  let panel = $state<HTMLDivElement>();
  let layer = $state(30);
  let panelLeft = $state(8);
  let panelTop = $state(8);
  let panelMaxWidth = $state<number | null>(null);
  let panelMaxHeight = $state<number | null>(null);
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
    const initial = panel?.querySelector<HTMLElement>(
      'select, [data-map-tool-close]',
    );
    (initial ?? panel)?.focus({ preventScroll: true });
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
  class="map-tool-trigger"
  data-map-regions-trigger={kind === 'regions' ? '' : undefined}
  data-map-sources-trigger={kind === 'sources' ? '' : undefined}
  data-map-selection-trigger={kind === 'selection' ? '' : undefined}
  aria-haspopup="dialog"
  aria-expanded={open}
  aria-controls={open ? dialogId : undefined}
  onclick={togglePanel}>{label}</button
>

{#if open}
  <div
    bind:this={panel}
    id={dialogId}
    class="map-tool-panel"
    data-map-regions-panel={kind === 'regions' ? '' : undefined}
    data-map-sources-panel={kind === 'sources' ? '' : undefined}
    data-map-selection-panel={kind === 'selection' ? '' : undefined}
    role="dialog"
    aria-modal="false"
    aria-labelledby={`${dialogId}-title`}
    tabindex="-1"
    style:left={`${panelLeft}px`}
    style:top={`${panelTop}px`}
    style:z-index={layer}
    style:max-width={panelMaxWidth === null ? undefined : `${panelMaxWidth}px`}
    style:max-height={panelMaxHeight === null
      ? undefined
      : `${panelMaxHeight}px`}
  >
    <header>
      <h2 id={`${dialogId}-title`}>{title}</h2>
      <button
        type="button"
        data-map-tool-close
        aria-label={`Close ${title.toLowerCase()}`}
        onclick={closePanel}>Close</button
      >
    </header>
    {@render children()}
  </div>
{/if}

<style>
  button {
    min-height: 2rem;
    padding: 0.3rem 0.4rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel);
    background-image: var(--panel-gloss);
    color: var(--text);
    font: inherit;
    font-size: 0.75rem;
    cursor: pointer;
  }
  button[aria-expanded='true'] {
    border-color: var(--neon);
    box-shadow: var(--line-glow);
  }
  button:focus-visible,
  .map-tool-panel:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .map-tool-panel {
    position: fixed;
    box-sizing: border-box;
    width: min(27rem, calc(100vw - 1rem));
    overflow: auto;
    overscroll-behavior: contain;
    padding: 0.75rem;
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
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
  }
  h2 {
    margin: 0;
    font-family: var(--font-hud);
    font-size: 1.05rem;
  }
</style>
