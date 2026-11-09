<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { untrack } from 'svelte';
  import {
    autocompleteSystems,
    type AutocompleteSystem,
  } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';

  let {
    selected = $bindable<AutocompleteSystem | null>(null),
    inputId = 'home-reference-system',
    invalid = false,
    describedBy,
  }: {
    selected?: AutocompleteSystem | null;
    inputId?: string;
    invalid?: boolean;
    describedBy?: string;
  } = $props();

  let input: HTMLInputElement;
  let query = $state(untrack(() => selected?.name ?? ''));
  let open = $state(false);
  let activeIndex = $state(-1);
  const normalizedQuery = $derived(query.trim());
  const listId = $derived(`${inputId}-suggestions`);
  // Match Explore's immediate, trimmed two-character lookup and shared cache.
  const suggestions = createQuery(() => ({
    queryKey: queryKeys.autocomplete(normalizedQuery.toLocaleLowerCase()),
    queryFn: ({ signal }) => autocompleteSystems(normalizedQuery, signal),
    enabled: normalizedQuery.length >= 2 && selected === null,
    staleTime: 60_000,
  }));
  const rows = $derived(
    suggestions.isError ? [] : (suggestions.data?.results ?? []),
  );
  const expanded = $derived(open && normalizedQuery.length >= 2);

  $effect(() => {
    if (expanded && rows[activeIndex]) {
      document
        .getElementById(`${listId}-${activeIndex}`)
        ?.scrollIntoView?.({ block: 'nearest', behavior: 'instant' });
    }
  });

  export function focus(): void {
    input?.focus();
  }

  function choose(hit: AutocompleteSystem): void {
    selected = hit;
    query = hit.name;
    open = false;
    activeIndex = -1;
    input?.focus();
  }

  function handleInput(event: Event): void {
    selected = null;
    query = (event.currentTarget as HTMLInputElement).value;
    open = query.trim().length >= 2;
    activeIndex = -1;
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && expanded) {
      event.preventDefault();
      open = false;
      activeIndex = -1;
      return;
    }
    if (!rows.length || normalizedQuery.length < 2) return;
    if (!expanded) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        open = true;
        activeIndex = event.key === 'ArrowDown' ? 0 : rows.length - 1;
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % rows.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeIndex = activeIndex <= 0 ? rows.length - 1 : activeIndex - 1;
    } else if (event.key === 'Home') {
      event.preventDefault();
      activeIndex = 0;
    } else if (event.key === 'End') {
      event.preventDefault();
      activeIndex = rows.length - 1;
    } else if (event.key === 'Enter') {
      const hit = rows[Math.max(0, activeIndex)];
      if (hit) {
        event.preventDefault();
        choose(hit);
      }
    }
  }
</script>

<div class="reference-system">
  <label for={inputId}>Reference system</label>
  <input
    bind:this={input}
    id={inputId}
    class="control-size"
    type="search"
    value={query}
    placeholder="Try Sol, Achenar, Lave…"
    autocomplete="off"
    role="combobox"
    aria-autocomplete="list"
    aria-expanded={expanded}
    aria-controls={expanded ? listId : undefined}
    aria-activedescendant={expanded && rows[activeIndex]
      ? `${listId}-${activeIndex}`
      : undefined}
    aria-invalid={invalid || undefined}
    aria-describedby={describedBy}
    oninput={handleInput}
    onfocus={() => (open = selected === null && normalizedQuery.length >= 2)}
    onblur={() => (open = false)}
    onkeydown={handleKeydown}
  />
  {#if expanded}
    <div class="suggestions">
      {#if suggestions.isPending}
        <p role="status">Searching catalogue…</p>
      {:else if suggestions.isError}
        <p role="alert">System suggestions are unavailable. Try again later.</p>
      {:else if rows.length === 0}
        <p role="status">No matching systems.</p>
      {/if}
      <div
        id={listId}
        role="listbox"
        aria-label="System suggestions"
        aria-busy={suggestions.isPending}
      >
        {#each rows as hit, index (hit.id64)}
          <button
            id={`${listId}-${index}`}
            type="button"
            role="option"
            tabindex="-1"
            aria-selected={index === activeIndex}
            class:active={index === activeIndex}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => choose(hit)}
          >
            <span>{hit.name}</span>
            <code>{hit.id64}</code>
          </button>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .reference-system {
    position: relative;
    min-width: 0;
  }
  label {
    display: block;
    margin-bottom: 0.5rem;
    color: var(--text-muted);
    font-size: 0.75rem;
  }
  input {
    width: 100%;
    min-height: 3rem;
    padding: 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-sunken);
    color: var(--text);
  }
  input::placeholder {
    color: var(--text-muted);
  }
  input:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .suggestions {
    position: absolute;
    z-index: 5;
    top: calc(100% + 0.5rem);
    right: 0;
    left: 0;
    max-height: 18rem;
    overflow: auto;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
    background: var(--panel);
    box-shadow: var(--shadow-panel);
  }
  .suggestions p {
    margin: 0;
    padding: 0.875rem;
    color: var(--text);
    font-size: 0.875rem;
  }
  [role='option'] {
    display: grid;
    width: 100%;
    gap: 0.125rem;
    padding: 0.75rem;
    border: 0;
    border-radius: var(--radius-sm);
    background: var(--panel);
    color: var(--text);
    text-align: left;
    cursor: pointer;
    overflow-wrap: anywhere;
  }
  [role='option']:hover,
  [role='option'].active {
    background: var(--panel-raised);
    outline: 2px solid var(--focus);
    outline-offset: -2px;
  }
  code {
    color: var(--text-muted);
    font-family: var(--font-sans);
    font-size: 0.75rem;
  }
</style>
