<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  import { isTopmostPopout, registerPopout } from './popout-stack';

  let {
    id,
    title,
    open,
    active = true,
    trigger,
    describedby,
    kind,
    closeOnOutside = true,
    onclose,
    children,
  }: {
    id: string;
    title: string;
    open: boolean;
    active?: boolean;
    trigger?: HTMLButtonElement;
    describedby?: string;
    kind: 'sources' | 'evidence' | 'review';
    closeOnOutside?: boolean;
    onclose: () => void;
    children: Snippet;
  } = $props();

  let panel = $state<HTMLDivElement>();
  let panelLeft = $state(8);
  let panelTop = $state(8);
  let panelMaxHeight = $state<number | null>(null);
  let panelMaxWidth = $state<number | null>(null);
  let layer = $state(30);
  let context: Element | null = null;

  // Retain the Sources & freshness positioning contract for zoom, mobile and
  // visual viewports. This is a non-modal tool: it never traps focus.
  function positionPanel() {
    if (!panel || !trigger || !active) return;
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
    panelMaxHeight = Math.max(0, Math.floor(height - margin * 2));
    panelMaxWidth = Math.max(0, Math.floor(width - margin * 2));
    const maxLeft = Math.max(
      left + margin,
      left + width - bounds.width - margin,
    );
    const maxTop = Math.max(
      top + margin,
      top + height - bounds.height - margin,
    );
    if (width < 640) {
      panelLeft = Math.max(left + margin, left + (width - bounds.width) / 2);
      panelTop = Math.max(top + margin, top + (height - bounds.height) / 2);
      return;
    }
    panelLeft = Math.max(left + margin, Math.min(anchor.left, maxLeft));
    const below = anchor.bottom + margin;
    panelTop = Math.max(
      top + margin,
      Math.min(
        below <= maxTop ? below : anchor.top - bounds.height - margin,
        maxTop,
      ),
    );
  }

  function close() {
    onclose();
    const fallback = context?.querySelector<HTMLElement>(
      '[role="tab"][aria-selected="true"]',
    );
    const target =
      trigger?.isConnected && !trigger.closest('[hidden], [inert]')
        ? trigger
        : fallback;
    target?.focus({ preventScroll: true });
  }

  function topmost(): boolean {
    return open && active && !!panel && isTopmostPopout(panel);
  }

  function keydown(event: KeyboardEvent) {
    if (!topmost() || event.key !== 'Escape') return;
    event.preventDefault();
    // Stop other capture listeners on window as well as the enclosing overlay.
    event.stopImmediatePropagation();
    close();
  }

  function outsideClick(event: MouseEvent) {
    if (!closeOnOutside || !topmost() || !(event.target instanceof Node))
      return;
    const element =
      event.target instanceof Element
        ? event.target
        : event.target.parentElement;
    // Task changes retain open tools. Other tool triggers can open a second
    // non-modal pop-out, and Why buttons can replace the current economy.
    if (element?.closest('[role="tab"], [data-inspect-popout-trigger]')) return;
    if (!panel?.contains(event.target) && !trigger?.contains(event.target))
      close();
  }

  $effect(() => {
    if (!open || !trigger) return;
    context = trigger.closest('[data-system-id64], .plan-workspace');
    // Read active only after the tick: returning to a tab reveals the retained
    // tool without moving focus away from the tab that the user just selected.
    void tick().then(() => {
      if (!open || !active) return;
      positionPanel();
      panel?.focus({ preventScroll: true });
    });
  });

  $effect(() => {
    const element = panel;
    if (!open || !active || !element) return;
    const unregister = registerPopout(element, (value) => (layer = value));
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
    window.addEventListener('keydown', keydown, true);
    window.addEventListener('click', outsideClick);
    window.addEventListener('scroll', scroll, true);
    window.addEventListener('resize', positionPanel);
    viewport?.addEventListener('resize', positionPanel);
    viewport?.addEventListener('scroll', positionPanel);
    return () => {
      unregister();
      observer?.disconnect();
      window.removeEventListener('keydown', keydown, true);
      window.removeEventListener('click', outsideClick);
      window.removeEventListener('scroll', scroll, true);
      window.removeEventListener('resize', positionPanel);
      viewport?.removeEventListener('resize', positionPanel);
      viewport?.removeEventListener('scroll', positionPanel);
    };
  });
</script>

{#if open}
  <div
    bind:this={panel}
    {id}
    class="inspect-popout {kind}-dialog"
    data-evidence-popout={kind === 'evidence' ? '' : undefined}
    data-review-export={kind === 'review' ? '' : undefined}
    role="dialog"
    aria-modal="false"
    aria-labelledby={`${id}-title`}
    aria-describedby={describedby}
    tabindex="-1"
    hidden={!active}
    inert={!active}
    style:left={`${panelLeft}px`}
    style:top={`${panelTop}px`}
    style:z-index={layer}
    style:max-height={panelMaxHeight === null
      ? undefined
      : `${panelMaxHeight}px`}
    style:max-width={panelMaxWidth === null ? undefined : `${panelMaxWidth}px`}
  >
    <header>
      <h2 id={`${id}-title`}>{title}</h2>
      <button type="button" onclick={close}>Close</button>
    </header>
    {@render children()}
  </div>
{/if}

<style>
  .inspect-popout {
    position: fixed;
    box-sizing: border-box;
    width: min(38rem, calc(100% - 1rem));
    max-height: calc(100dvh - 1rem);
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 0.9rem;
    border: 1px solid var(--neon);
    border-radius: var(--radius, 0.7rem);
    background: var(--panel);
    box-shadow: var(--shadow-panel);
    font-size: 0.85rem;
    font-weight: normal;
    line-height: 1.45;
  }
  .inspect-popout[hidden] {
    display: none;
  }
  .sources-dialog {
    width: min(32rem, calc(100% - 1rem));
  }
  /* Review & export sets its summary beside the unresolved items. */
  .review-dialog {
    width: min(60rem, calc(100% - 1rem));
  }
  header {
    position: sticky;
    top: -0.9rem;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.65rem;
    padding-block: 0.3rem;
    background: var(--panel);
  }
  h2 {
    margin: 0;
    font-family: var(--font-hud, var(--font-sans, sans-serif));
    font-size: 1.05rem;
  }
  button {
    padding: 0.35rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: var(--radius, 0.4rem);
    background: var(--panel-raised);
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  button:focus-visible,
  .inspect-popout:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
