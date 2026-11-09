<script lang="ts">
  import type { SchematicItem } from './schematic';

  let {
    items,
    selectedRef = null,
  }: {
    items: SchematicItem[];
    /** The drawn item the selected body belongs to, if any. */
    selectedRef?: string | null;
  } = $props();

  function moonNote(count: number): string {
    return `+${count} ${count === 1 ? 'moon' : 'moons'}`;
  }
  /** Drawn size only hints at relative size; the schematic is not to scale. */
  function discSize(item: SchematicItem): number {
    if (item.kind === 'star') return 44;
    if (item.kind === 'belt') return 34;
    return item.radius_km === null
      ? 26
      : Math.min(
          40,
          Math.max(24, 16 + Math.log10(Math.max(1, item.radius_km)) * 6),
        );
  }

  const marked = $derived(items.filter((item) => item.marker));
  const summary = $derived(
    `System layout schematic, not to scale: ${items
      .map(
        (item) => item.name + (item.moons ? ` (${moonNote(item.moons)})` : ''),
      )
      .join(', ')}. ${
      marked.length
        ? `Builds in the plan: ${marked
            .map((item) => `${item.marker} on ${item.name}`)
            .join('; ')}.`
        : 'No builds are planned on these bodies yet.'
    }`,
  );
</script>

<figure class="plan-schematic" data-plan-schematic>
  <figcaption>
    <span
      >System layout <small class="plan-muted">· schematic not to scale</small
      ></span
    >
    <span class="schematic-legend" aria-hidden="true">
      <span><i class="legend-observed"></i> Observed body</span>
      <span><i class="legend-planned"></i> Planned facility</span>
    </span>
  </figcaption>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll a long system sideways.) -->
  <div class="schematic-scroll" role="img" aria-label={summary} tabindex="0">
    <ol class="schematic-line" aria-hidden="true">
      {#each items as item (item.body_ref)}
        <li
          class="schematic-item"
          class:selected={selectedRef === item.body_ref}
          data-schematic-body={item.body_ref}
        >
          <span class="schematic-marker" data-schematic-marker={!!item.marker}>
            {#if item.marker}
              <span class="marker-label" class:is-primary={item.primary}
                >{item.marker}</span
              >
              <i class="marker-ring"></i>
              <i class="marker-link"></i>
            {/if}
          </span>
          <span class="schematic-disc-cell">
            <span
              class="schematic-disc"
              class:star={item.kind === 'star'}
              class:belt={item.kind === 'belt'}
              style={`--disc-size: ${discSize(item)}px`}
            ></span>
          </span>
          <span class="schematic-name" title={item.name}>{item.name}</span>
          {#if item.moons}<span class="schematic-note"
              >{moonNote(item.moons)}</span
            >{/if}
        </li>
      {/each}
    </ol>
  </div>
</figure>

<style>
  .plan-schematic {
    --marker-height: 3.6rem;
    --disc-row: 3rem;
    margin: 0.75rem 0;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--panel-sunken);
  }
  figcaption {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.35rem 1rem;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--line);
    font-size: 0.85rem;
    font-weight: 650;
  }
  figcaption small {
    font-weight: 400;
  }
  .schematic-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 1rem;
    color: var(--text-muted);
    font-size: 0.75rem;
    font-weight: 400;
  }
  .schematic-legend i {
    display: inline-block;
    width: 0.7rem;
    height: 0.7rem;
    margin-right: 0.25rem;
    border-radius: 50%;
    vertical-align: -0.05rem;
  }
  .legend-observed {
    background: var(--text);
  }
  .legend-planned {
    border: 1.5px dashed var(--neon);
  }
  .schematic-scroll {
    overflow-x: auto;
    padding: 0.5rem 0.75rem 0.75rem;
  }
  .schematic-scroll:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: -2px;
  }
  .schematic-line {
    position: relative;
    display: flex;
    width: max-content;
    min-width: 100%;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  /* The orbit line runs through the middle of every disc. */
  .schematic-line::before {
    content: '';
    position: absolute;
    top: calc(var(--marker-height) + var(--disc-row) / 2);
    right: 0;
    left: 0;
    border-top: 1px solid var(--line-strong);
  }
  .schematic-item {
    position: relative;
    display: grid;
    grid-template-rows: var(--marker-height) var(--disc-row) auto auto;
    justify-items: center;
    flex: 1 0 5rem;
    min-width: 5rem;
    max-width: 7rem;
    padding: 0 0.15rem;
    text-align: center;
  }
  .schematic-marker {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    width: 100%;
  }
  .marker-label {
    max-width: 100%;
    padding-bottom: 0.15rem;
    overflow: hidden;
    color: var(--neon-hi);
    font-size: 0.7rem;
    line-height: 1.15;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow-wrap: anywhere;
  }
  .marker-label.is-primary {
    color: var(--accent-hi);
    font-weight: 650;
  }
  .marker-ring {
    width: 1rem;
    height: 1rem;
    border: 1.5px dashed var(--neon);
    border-radius: 50%;
  }
  .marker-link {
    height: 0.45rem;
    border-left: 1.5px dashed var(--neon);
  }
  .schematic-disc-cell {
    display: grid;
    place-items: center;
    position: relative;
  }
  .schematic-disc {
    display: block;
    width: var(--disc-size);
    height: var(--disc-size);
    border-radius: var(--radius-pill);
    background: radial-gradient(
      circle at 30% 25%,
      var(--neon),
      var(--line-strong) 65%,
      var(--panel-sunken)
    );
    border: 1px solid var(--neon);
  }
  .schematic-disc.star {
    background: radial-gradient(
      circle at 35% 30%,
      var(--warn),
      var(--accent-hi) 50%,
      var(--accent)
    );
    border-color: var(--accent-hi);
  }
  .schematic-disc.belt {
    background: repeating-radial-gradient(
      ellipse,
      var(--line) 0 2px,
      var(--text-muted) 3px 4px,
      transparent 5px 6px
    );
    transform: rotate(-25deg) scaleY(0.65);
    border: 2px dotted var(--text-muted);
  }
  .selected .schematic-disc {
    box-shadow:
      0 0 0 3px var(--panel-sunken),
      0 0 0 5px var(--accent);
  }
  .schematic-name {
    max-width: 100%;
    margin-top: 0.15rem;
    overflow: hidden;
    font-size: 0.78rem;
    font-weight: 650;
    line-height: 1.2;
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  .selected .schematic-name {
    color: var(--accent);
  }
  .schematic-note {
    color: var(--text-muted);
    font-size: 0.7rem;
  }
</style>
