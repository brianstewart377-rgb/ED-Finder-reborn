<script lang="ts">
  import { tick } from 'svelte';
  import {
    isTopmostPopout,
    registerPopout,
  } from '$lib/components/popout-stack';
  import {
    BODY_FILTER_GROUPS,
    BODY_FILTER_OPTIONS,
    HARD_BODY_COUNT_MAX,
    bodyFilterLabel,
    normalizeBodyFilters,
    type BodyFilterKey,
    type BodyFilterValues,
  } from './body-filters';

  let {
    value,
    disabled = false,
    paused = false,
    onapply,
    onremove,
    onclear,
  }: {
    value: BodyFilterValues;
    disabled?: boolean;
    paused?: boolean;
    onapply: (value: BodyFilterValues) => void;
    onremove: (key: BodyFilterKey) => void;
    onclear: () => void;
  } = $props();

  const id = $props.id();
  const explanation =
    'Body filters work when you search near a system with Any selected.';
  let open = $state(false);
  let draft = $state<BodyFilterValues>({});
  let activeTab = $state(0);
  let trigger = $state<HTMLButtonElement>();
  let panel = $state<HTMLDivElement>();
  let layer = $state(30);
  let panelLeft = $state(8);
  let panelTop = $state(8);
  let panelMaxHeight = $state<number | null>(null);
  let panelMaxWidth = $state<number | null>(null);
  // A zero minimum with no maximum filters nothing: it is not counted, applied
  // or shown as a chip, while an exact zero range (min 0, max 0) still is.
  function effective(values: BodyFilterValues): BodyFilterValues {
    const output: BodyFilterValues = {};
    for (const option of BODY_FILTER_OPTIONS) {
      const range = values[option.key];
      if (!range || (range.min === 0 && range.max === undefined)) continue;
      output[option.key] = range;
    }
    return output;
  }
  const draftCount = $derived(Object.keys(effective(draft)).length);
  const applied = $derived(
    BODY_FILTER_OPTIONS.filter((option) => value[option.key] !== undefined),
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
    open = false;
    trigger?.focus({ preventScroll: true });
  }

  async function toggle() {
    if (open) {
      close();
      return;
    }
    draft = normalizeBodyFilters(value);
    open = true;
    await tick();
    if (!open) return;
    positionPanel();
    panel
      ?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')
      ?.focus({ preventScroll: true });
  }

  function updateBound(key: BodyFilterKey, bound: 'min' | 'max', raw: string) {
    const range = { ...draft[key] };
    if (!/^\d+$/.test(raw)) delete range[bound];
    else range[bound] = Math.min(HARD_BODY_COUNT_MAX, Number(raw));
    // Moving either endpoint carries the other along when they cross.
    if (
      range.min !== undefined &&
      range.max !== undefined &&
      range.min > range.max
    ) {
      if (bound === 'min') range.max = range.min;
      else range.min = range.max;
    }
    const next = { ...draft };
    if (range.min === undefined && range.max === undefined) delete next[key];
    else next[key] = range;
    draft = next;
  }

  function numberInput(
    key: BodyFilterKey,
    bound: 'min' | 'max',
    input: HTMLInputElement,
  ) {
    updateBound(key, bound, input.value);
    input.value = draft[key]?.[bound]?.toString() ?? '';
  }

  function setAny(key: BodyFilterKey, any: boolean, sliderMax: number) {
    updateBound(
      key,
      'max',
      any ? '' : String(Math.max(sliderMax, draft[key]?.min ?? 0)),
    );
  }

  async function tabKeydown(event: KeyboardEvent) {
    const count = BODY_FILTER_GROUPS.length;
    let next: number;
    if (event.key === 'ArrowRight') next = (activeTab + 1) % count;
    else if (event.key === 'ArrowLeft') next = (activeTab + count - 1) % count;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = count - 1;
    else return;
    event.preventDefault();
    activeTab = next;
    await tick();
    panel
      ?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')
      ?.focus();
  }

  function keydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape' && isTopmostPopout(panel)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
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
      close();
  }

  $effect(() => {
    if (disabled && open) close();
  });

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

<div class="body-filters">
  <button
    bind:this={trigger}
    type="button"
    class="kit-pill"
    {disabled}
    aria-haspopup="dialog"
    aria-expanded={open}
    aria-controls={open ? `${id}-dialog` : undefined}
    aria-describedby={disabled ? `${id}-disabled` : undefined}
    onclick={toggle}>Body filters</button
  >
  {#if disabled}<p id={`${id}-disabled`} class="hint">{explanation}</p>{/if}
  {#if applied.length}
    <div
      class="applied-filters kit-chip-row"
      class:paused
      role="group"
      aria-label={paused ? 'Paused body filters' : 'Applied body filters'}
      aria-describedby={paused && disabled ? `${id}-disabled` : undefined}
    >
      {#each applied as option (option.key)}
        <span class="filter-chip kit-chip">
          <span>{bodyFilterLabel(option.key, value[option.key]!)}</span>
          <span class="chip-state" class:paused-label={paused}
            >{paused ? 'Paused' : 'Applied'}</span
          >
          <button
            type="button"
            aria-label={`Remove ${option.label} filter`}
            onclick={() => onremove(option.key)}>×</button
          >
        </span>
      {/each}
      <button type="button" class="clear-all kit-pill" onclick={() => onclear()}
        >Clear all</button
      >
    </div>
  {/if}
</div>

{#if open}
  <div
    bind:this={panel}
    id={`${id}-dialog`}
    class="body-filters-dialog"
    data-testid="body-filters-dialog"
    role="dialog"
    aria-modal="false"
    aria-labelledby={`${id}-title`}
    aria-describedby={`${id}-help`}
    tabindex="-1"
    style:left={`${panelLeft}px`}
    style:z-index={layer}
    style:top={`${panelTop}px`}
    style:max-height={panelMaxHeight === null
      ? undefined
      : `min(42rem, ${panelMaxHeight}px)`}
    style:max-width={panelMaxWidth === null ? undefined : `${panelMaxWidth}px`}
  >
    <header>
      <h2 id={`${id}-title`}>Body filters</h2>
      <button type="button" class="kit-pill" onclick={close}>Close</button>
    </header>
    <p id={`${id}-help`} class="hint panel-help">
      Choose whole counts. Type up to 999; Any removes the maximum.
    </p>
    <div class="tabs" role="tablist" aria-label="Body filter categories">
      {#each BODY_FILTER_GROUPS as group, index (group.label)}
        {@const count = group.filters.filter(
          (option) => effective(draft)[option.key] !== undefined,
        ).length}
        <button
          id={`${id}-tab-${index}`}
          type="button"
          role="tab"
          aria-selected={activeTab === index}
          aria-controls={`${id}-panel-${index}`}
          tabindex={activeTab === index ? 0 : -1}
          onclick={() => (activeTab = index)}
          onkeydown={tabKeydown}
          >{group.label} <span class="badge">{count}</span></button
        >
      {/each}
    </div>
    {#each BODY_FILTER_GROUPS as group, index (group.label)}
      <div
        id={`${id}-panel-${index}`}
        class="filter-options"
        role="tabpanel"
        aria-labelledby={`${id}-tab-${index}`}
        hidden={activeTab !== index}
      >
        {#each group.filters as option (option.key)}
          {@const range = draft[option.key]}
          <fieldset>
            <legend>{option.label}</legend>
            <div class="range-controls">
              <div class="endpoint">
                <span>Minimum</span>
                <input
                  type="range"
                  aria-label={`${option.label} minimum slider`}
                  min="0"
                  max={option.sliderMax}
                  step="1"
                  value={Math.min(range?.min ?? 0, option.sliderMax)}
                  aria-valuetext={range?.min === undefined
                    ? 'No minimum'
                    : String(range.min)}
                  oninput={(event) =>
                    updateBound(option.key, 'min', event.currentTarget.value)}
                />
                <input
                  type="number"
                  aria-label={`${option.label} minimum`}
                  min="0"
                  max={HARD_BODY_COUNT_MAX}
                  step="1"
                  placeholder="0"
                  value={range?.min ?? ''}
                  oninput={(event) =>
                    numberInput(option.key, 'min', event.currentTarget)}
                />
              </div>
              <div class="endpoint">
                <span>Maximum</span>
                <input
                  type="range"
                  aria-label={`${option.label} maximum slider`}
                  min="0"
                  max={option.sliderMax}
                  step="1"
                  disabled={range?.max === undefined}
                  value={Math.min(
                    range?.max ?? option.sliderMax,
                    option.sliderMax,
                  )}
                  aria-valuetext={range?.max === undefined
                    ? 'Any'
                    : String(range.max)}
                  oninput={(event) =>
                    updateBound(option.key, 'max', event.currentTarget.value)}
                />
                <input
                  type="number"
                  aria-label={`${option.label} maximum`}
                  min="0"
                  max={HARD_BODY_COUNT_MAX}
                  step="1"
                  placeholder="Any"
                  value={range?.max ?? ''}
                  oninput={(event) =>
                    numberInput(option.key, 'max', event.currentTarget)}
                />
              </div>
              <label class="any-maximum">
                <input
                  type="checkbox"
                  aria-label={`${option.label} Any maximum`}
                  checked={range?.max === undefined}
                  onchange={(event) =>
                    setAny(
                      option.key,
                      event.currentTarget.checked,
                      option.sliderMax,
                    )}
                />
                Any
              </label>
            </div>
          </fieldset>
        {/each}
      </div>
    {/each}
    <footer>
      <span aria-live="polite">{draftCount} filters selected</span>
      <div class="footer-actions">
        <button type="button" class="kit-pill" onclick={() => (draft = {})}
          >Reset</button
        >
        <button type="button" class="kit-pill" onclick={close}>Cancel</button>
        <button
          type="button"
          class="apply kit-pill"
          onclick={() => {
            onapply(normalizeBodyFilters(effective(draft)));
            close();
          }}>Apply</button
        >
      </div>
    </footer>
  </div>
{/if}

<style>
  .body-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
    min-width: 0;
    max-width: 24rem;
  }
  button {
    padding: 0.5rem 0.75rem;
    font: inherit;
    cursor: pointer;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .hint {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.4;
  }
  .applied-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    flex-basis: 100%;
  }
  .filter-chip {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.35rem;
    max-width: 100%;
    padding: 0.25rem 0.25rem 0.25rem 0.65rem;
    font-size: 0.85rem;
    line-height: 1.4;
  }
  .filter-chip > span:first-child {
    overflow-wrap: anywhere;
  }
  .filter-chip button {
    flex-shrink: 0;
    min-width: 1.75rem;
    min-height: 1.75rem;
    border: 0;
    padding: 0.1rem 0.4rem;
  }
  .paused .filter-chip {
    border-style: dashed;
  }
  .chip-state {
    color: var(--text-muted);
    font-size: 0.75rem;
  }
  .paused-label {
    padding: 0.1rem 0.35rem;
  }
  .clear-all {
    font-size: 0.85rem;
  }
  .body-filters-dialog {
    position: fixed;
    z-index: 30;
    display: flex;
    flex-direction: column;
    width: min(40rem, calc(100% - 1rem));
    max-height: min(42rem, calc(100dvh - 1rem));
    overflow: hidden;
  }
  header,
  footer {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
  }
  h2 {
    margin: 0;
    font-size: 1.15rem;
  }
  .panel-help {
    flex-shrink: 0;
    padding: 0 1rem 0.75rem;
  }
  .tabs {
    display: flex;
    flex-shrink: 0;
    gap: 0.35rem;
    padding: 0 1rem 0.75rem;
    margin-bottom: 0.75rem;
  }
  .tabs button {
    flex: 1;
    padding-inline: 0.4rem;
  }
  .badge {
    display: inline-block;
    min-width: 1.2em;
    margin-left: 0.2rem;
    padding-inline: 0.2em;
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    font-size: 0.8rem;
  }
  .filter-options {
    min-height: 0;
    overflow-y: auto;
    padding: 0 1rem;
    overscroll-behavior: contain;
  }
  .filter-options[hidden] {
    display: none;
  }
  fieldset {
    min-width: 0;
    margin: 0 0 0.8rem;
    padding: 0.6rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
  }
  legend {
    padding-inline: 0.3rem;
    font-weight: 600;
  }
  .range-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
    align-items: end;
    gap: 0.7rem;
  }
  .endpoint {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 4.5rem;
    align-items: center;
    gap: 0.4rem;
    min-width: 0;
  }
  .endpoint > span {
    grid-column: 1 / -1;
    color: var(--text-muted);
    font-size: 0.8rem;
  }
  input {
    min-width: 0;
    accent-color: var(--accent);
  }
  input[type='range'] {
    width: 100%;
    min-height: 1.5rem;
    margin: 0;
  }
  input[type='number'] {
    width: 100%;
    min-height: 2.25rem;
    padding: 0.35rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    color: inherit;
    font: inherit;
  }
  .any-maximum {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    min-height: 2.25rem;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  footer {
    flex-wrap: wrap;
    border-top: 1px solid var(--line);
    font-size: 0.85rem;
    background: var(--panel-sunken);
  }
  .footer-actions {
    display: flex;
    gap: 0.5rem;
    margin-left: auto;
  }
  @media (max-width: 639px) {
    .endpoint {
      grid-template-columns: minmax(0, 1fr);
    }
    .range-controls {
      gap: 0.6rem;
    }
  }
</style>
