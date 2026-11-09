<script lang="ts">
  import type { GalaxySystemPoint, Vec3Ly } from './contracts';

  let {
    system,
    anchor = null,
    onSelect,
    inspectHref,
    onInspect,
    onDismiss,
    id = 'galaxy-system-tooltip',
  } = $props<{
    system: GalaxySystemPoint;
    anchor?: { name: string; positionLy: Vec3Ly } | null;
    onSelect: (system: GalaxySystemPoint) => void;
    /** Resolved by the application so its anchor/navigation context survives. */
    inspectHref: string;
    onInspect?: (event: MouseEvent, system: GalaxySystemPoint) => void;
    onDismiss?: () => void;
    id?: string;
  }>();

  const distanceLy = $derived.by(() => {
    if (!anchor) return null;
    const distance = Math.hypot(
      system.positionLy.x - anchor.positionLy.x,
      system.positionLy.y - anchor.positionLy.y,
      system.positionLy.z - anchor.positionLy.z,
    );
    return Number.isFinite(distance) ? distance : null;
  });

  function dismissOnEscape(event: KeyboardEvent): void {
    if (event.key === 'Escape') onDismiss?.();
  }
</script>

<svelte:window onkeydown={dismissOnEscape} />

<div
  {id}
  class="galaxy-system-hover-card"
  role="tooltip"
  aria-label={`System ${system.name}`}
  data-system-id64={system.systemId64}
>
  <strong>{system.name}</strong>
  {#if system.primaryStar?.type || system.primaryStar?.subtype || (anchor && distanceLy !== null)}
    <dl>
      {#if system.primaryStar?.type}
        <div>
          <dt>Star class</dt>
          <dd>{system.primaryStar.type}</dd>
        </div>
      {/if}
      {#if system.primaryStar?.subtype}
        <div>
          <dt>Star subtype</dt>
          <dd>{system.primaryStar.subtype}</dd>
        </div>
      {/if}
      {#if anchor && distanceLy !== null}
        <div>
          <dt>Distance from {anchor.name}</dt>
          <dd>{distanceLy.toFixed(1)} LY</dd>
        </div>
      {/if}
    </dl>
  {/if}
  <div class="galaxy-system-hover-card__actions">
    <button type="button" onclick={() => onSelect(system)}>Select</button>
    <!-- eslint-disable svelte/no-navigation-without-resolve -- the application passes its resolved Inspect href -->
    <a href={inspectHref} onclick={(event) => onInspect?.(event, system)}
      >Inspect</a
    >
    <!-- eslint-enable svelte/no-navigation-without-resolve -->
  </div>
</div>

<style>
  .galaxy-system-hover-card {
    width: 16rem;
    max-width: 100%;
    padding: 0.75rem;
    border: 1px solid #45627e;
    border-radius: 0.35rem;
    background: #0b1522;
    color: #e7eef5;
    box-shadow: 0 0.3rem 1rem #0008;
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }

  strong {
    display: block;
    color: #f6f9fb;
    font-size: 0.9rem;
  }

  dl {
    display: grid;
    gap: 0.5rem;
    margin: 0.6rem 0;
  }

  dt {
    color: #a6acb4;
    font-size: 0.72rem;
  }

  dd {
    margin: 0.15rem 0 0;
  }

  .galaxy-system-hover-card__actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.6rem;
  }

  button,
  a {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 2.2rem;
    padding: 0.35rem 0.6rem;
    border: 1px solid #45627e;
    border-radius: 0.2rem;
    background: transparent;
    color: inherit;
    font: inherit;
    text-decoration: none;
    cursor: pointer;
  }

  button:hover,
  a:hover {
    border-color: #7dd3fc;
    background: #173047;
  }

  button:focus-visible,
  a:focus-visible {
    outline: 2px solid #ff7a14;
    outline-offset: 2px;
  }
</style>
