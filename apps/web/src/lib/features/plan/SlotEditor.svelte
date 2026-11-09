<script lang="ts">
  import { tick } from 'svelte';
  import {
    isTopmostPopout,
    registerPopout,
  } from '$lib/components/popout-stack';

  type Props = {
    label: string;
    kind: 'orbital' | 'surface';
    used: number;
    value: number | null;
    source?: 'predicted' | 'entered by you';
    prediction?: number | null;
    disabled?: boolean;
    hidden?: boolean;
    onchange: (value: number | null) => void;
  };
  let {
    label,
    kind,
    used,
    value,
    source = 'entered by you',
    prediction = null,
    disabled = false,
    hidden = false,
    onchange,
  }: Props = $props();

  const id = $props.id();
  let open = $state(false);
  let draft = $state<number | null>(null);
  let editorLeft = $state(0);
  let editorTop = $state(0);
  let trigger: HTMLButtonElement;
  let editor = $state<HTMLDivElement>();
  let layer = $state(30);
  let container: HTMLDivElement;
  const overCapacity = $derived(value !== null && used > value);
  const slotLabel = $derived(kind === 'orbital' ? 'Orbital' : 'Surface');
  const showSource = $derived(
    kind === 'surface' && value !== null && !disabled,
  );

  async function toggle() {
    if (disabled) return;
    if (open) {
      close();
      return;
    }
    draft = value;
    open = true;
    await tick();
    if (!editor) return;
    const anchor = trigger.getBoundingClientRect();
    const bounds = editor.getBoundingClientRect();
    editorLeft = Math.max(
      8,
      Math.min(
        anchor.right - bounds.width,
        window.innerWidth - bounds.width - 8,
      ),
    );
    editorTop = Math.max(
      8,
      Math.min(anchor.bottom + 6, window.innerHeight - bounds.height - 8),
    );
    editor?.focus();
  }

  function close() {
    open = false;
    trigger?.focus({ preventScroll: true });
  }

  function change(next: number | null) {
    draft = next === null ? prediction : Math.min(64, Math.max(0, next));
    onchange(next === null ? null : draft);
  }

  function keydown(event: KeyboardEvent) {
    if (hidden || !open || !editor || !isTopmostPopout(editor)) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = Array.from(
      editor.querySelectorAll<HTMLButtonElement>('button:not([disabled])'),
    );
    const first = controls[0];
    const last = controls.at(-1);
    const active = document.activeElement;
    if (active === editor || !editor.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  function pointerdown(event: PointerEvent) {
    if (
      open &&
      !hidden &&
      event.target instanceof Node &&
      !container.contains(event.target) &&
      // Switching task tabs retains the editor and its draft. The hidden
      // panel suspends these global handlers until System layout is shown.
      !(
        event.target instanceof Element &&
        event.target.closest('[role="tablist"][aria-label="Plan tasks"]')
      )
    ) {
      // Pointer users retain focus on the control they clicked outside.
      open = false;
    }
  }

  $effect(() => {
    if (!open || hidden || !editor) return;
    return registerPopout(editor, (value) => (layer = value));
  });
</script>

<svelte:window onkeydown={keydown} onpointerdown={pointerdown} />

<div class="slot-container" bind:this={container} {hidden} inert={hidden}>
  <button
    bind:this={trigger}
    type="button"
    class="slot-box"
    data-slot-kind={kind}
    class:over-capacity={overCapacity && !disabled}
    {disabled}
    aria-label={disabled
      ? `${slotLabel} slots on ${label}: not available`
      : `${slotLabel} slots on ${label}: ${used} used, ${value ?? 'unknown'} total${overCapacity ? ', over capacity' : ''}`}
    aria-haspopup="dialog"
    aria-expanded={open}
    aria-controls={open ? `${id}-editor` : undefined}
    aria-describedby={showSource ? `${id}-source` : undefined}
    title={disabled
      ? `${slotLabel} slots on ${label}: not available`
      : `${slotLabel} slots on ${label}: ${used} used, ${value ?? 'unknown'} total${overCapacity ? ', over capacity' : ''}`}
    onclick={toggle}
  >
    <span aria-hidden="true">{kind === 'orbital' ? '◌' : '◍'}</span>
    <strong>{disabled ? '–' : `${used}/${value ?? '?'}`}</strong>
    {#if overCapacity && !disabled}<span class="warning" aria-hidden="true"
        >!</span
      ><span class="slot-sr-only">Over capacity</span>{/if}
  </button>
  {#if showSource}<span class="slot-source" id={`${id}-source`}>{source}</span
    >{/if}
  {#if open}
    <div
      bind:this={editor}
      class="slot-editor"
      style:z-index={layer}
      style:left={`${editorLeft}px`}
      style:top={`${editorTop}px`}
      id={`${id}-editor`}
      role="dialog"
      tabindex="-1"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-help`}
    >
      <h4 id={`${id}-title`}>{slotLabel} slots on {label}</h4>
      <p id={`${id}-help`}>
        {#if kind === 'orbital'}This value is entered by the commander. Copy
          orbital slots from Architect Mode.
        {:else if value !== null && source === 'predicted'}Predicted slots —
          high-accuracy algorithm, not guaranteed. Verify in Architect Mode.
        {:else if value !== null}This value was entered by you.
        {:else}Surface slots are unknown. Verify in Architect Mode.{/if}
        Enter 0–64{prediction !== null
          ? ', or use the prediction.'
          : ', or leave it unknown.'}
      </p>
      <div class="stepper">
        <button
          type="button"
          aria-label={`Decrease ${kind} slots`}
          disabled={draft === 0}
          onclick={() => change((draft ?? 1) - 1)}>−</button
        >
        <output aria-live="polite" aria-label={`${slotLabel} slot total`}
          >{draft ?? 'Unknown'}</output
        >
        <button
          type="button"
          aria-label={`Increase ${kind} slots`}
          disabled={draft === 64}
          onclick={() => change((draft ?? 0) + 1)}>+</button
        >
      </div>
      <div class="editor-actions">
        <button type="button" onclick={() => change(null)}
          >{prediction !== null ? 'Use prediction' : 'Unknown'}</button
        >
        <button type="button" onclick={close}>Done</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .slot-container {
    position: relative;
    flex: 0 0 auto;
  }
  .slot-source {
    display: block;
    color: var(--text-muted);
    font-size: 0.6rem;
    line-height: 1.2;
    text-align: center;
    white-space: nowrap;
  }
  .slot-box {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.2rem;
    min-width: 2.65rem;
    height: 24px;
    padding: 0 0.25rem;
    text-align: center;
    white-space: nowrap;
    font-size: 0.65rem;
  }
  .slot-box strong {
    font-size: inherit;
    font-variant-numeric: tabular-nums;
  }
  .over-capacity {
    border-color: var(--accent);
    box-shadow: inset 0 0 0 1px var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, var(--panel));
  }
  .warning {
    font-size: 0.65rem;
    font-weight: 650;
  }
  .slot-sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .slot-editor {
    position: fixed;
    z-index: 20;
    width: min(17rem, calc(100vw - 3rem));
    max-height: calc(100dvh - 1rem);
    overflow-y: auto;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    padding: 0.9rem;
    background: var(--panel);
    background-image: var(--panel-gloss);
    box-shadow: var(--shadow-panel), var(--line-glow);
  }
  h4 {
    margin: 0;
    font-size: 0.9rem;
  }
  p {
    font-size: 0.8rem;
    line-height: 1.5;
    color: var(--text-muted);
  }
  .stepper,
  .editor-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .stepper button {
    min-width: 2.75rem;
    min-height: 2.75rem;
    font-size: 1.15rem;
  }
  .stepper output {
    font-variant-numeric: tabular-nums;
  }
  .editor-actions {
    margin-top: 0.75rem;
  }
  .editor-actions button {
    min-height: 2.75rem;
    padding: 0.4rem 0.65rem;
    font-size: 0.85rem;
  }
</style>
