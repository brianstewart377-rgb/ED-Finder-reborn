<script lang="ts">
  // The Finder's system-name box and the suggestions under it. The Explore
  // page decides what choosing a system does, and what emptying the box does.
  // Moved here from ExploreWorkspace.svelte on 15 November 2026; its looks
  // come from app.css (.combobox-wrap, .search-control, .suggestions).
  import { createQuery } from '@tanstack/svelte-query';
  import { Search } from '@lucide/svelte';
  import {
    autocompleteSystems,
    type AutocompleteSystem,
  } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';

  let {
    query = $bindable(''),
    onchoose,
    onemptied,
  }: {
    /** What is typed in the box. */
    query?: string;
    /** A suggestion was chosen, by click or with Enter. */
    onchoose: (hit: AutocompleteSystem) => void;
    /** The box was emptied by typing. */
    onemptied: () => void;
  } = $props();

  let activeSuggestion = $state(-1);
  let autocompleteOpen = $state(false);
  let searchInput = $state<HTMLInputElement>();
  const normalizedQuery = $derived(query.trim());
  const suggestions = createQuery(() => ({
    queryKey: queryKeys.autocomplete(normalizedQuery.toLocaleLowerCase()),
    queryFn: ({ signal }) => autocompleteSystems(normalizedQuery, signal),
    enabled: normalizedQuery.length >= 2,
    staleTime: 60_000,
  }));
  const suggestionRows = $derived(suggestions.data?.results ?? []);

  /** Puts the cursor in the box. */
  export function focus(options?: FocusOptions): void {
    searchInput?.focus(options);
  }

  /** Closes the suggestions. */
  export function close(): void {
    autocompleteOpen = false;
  }

  function chooseSuggestion(hit: AutocompleteSystem): void {
    query = hit.name;
    autocompleteOpen = false;
    onchoose(hit);
  }

  function handleQueryInput(event: Event): void {
    if (!(event.currentTarget as HTMLInputElement).value.trim()) onemptied();
    autocompleteOpen =
      (event.currentTarget as HTMLInputElement).value.trim().length >= 2;
    activeSuggestion = -1;
  }

  function handleQueryKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && autocompleteOpen) {
      autocompleteOpen = false;
      event.preventDefault();
      return;
    }
    if (!autocompleteOpen || !suggestionRows.length) {
      if (event.key === 'ArrowDown' && suggestionRows.length) {
        autocompleteOpen = true;
        event.preventDefault();
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeSuggestion = (activeSuggestion + 1) % suggestionRows.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeSuggestion =
        activeSuggestion <= 0
          ? suggestionRows.length - 1
          : activeSuggestion - 1;
    } else if (event.key === 'Home') {
      event.preventDefault();
      activeSuggestion = 0;
    } else if (event.key === 'End') {
      event.preventDefault();
      activeSuggestion = suggestionRows.length - 1;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const hit = suggestionRows[Math.max(0, activeSuggestion)];
      if (hit) chooseSuggestion(hit);
    }
  }
</script>

<div class="combobox-wrap">
  <label for="system-search">System name</label>
  <div class="search-control">
    <Search aria-hidden="true" size={19} />
    <input
      id="system-search"
      type="search"
      placeholder="Try Sol, Achenar, Lave…"
      autocomplete="off"
      role="combobox"
      aria-autocomplete="list"
      aria-expanded={autocompleteOpen && normalizedQuery.length >= 2}
      aria-controls="system-suggestions"
      aria-activedescendant={autocompleteOpen &&
      suggestionRows[activeSuggestion]
        ? `suggestion-${activeSuggestion}`
        : undefined}
      bind:this={searchInput}
      bind:value={query}
      oninput={handleQueryInput}
      onfocus={() => (autocompleteOpen = normalizedQuery.length >= 2)}
      onkeydown={handleQueryKeydown}
    />
  </div>
  {#if autocompleteOpen && normalizedQuery.length >= 2}
    <div
      id="system-suggestions"
      class="suggestions"
      role="listbox"
      aria-label="System suggestions"
    >
      {#if suggestions.isPending}
        <p role="status">Searching catalogue…</p>
      {:else if suggestions.isError}
        <div role="alert">
          <p>Typeahead is unavailable.</p>
          <button type="button" onclick={() => suggestions.refetch()}
            >Retry</button
          >
        </div>
      {:else if suggestionRows.length === 0}
        <p>No matching systems.</p>
      {:else}
        {#each suggestionRows as hit, index (hit.id64)}
          <button
            id={`suggestion-${index}`}
            type="button"
            role="option"
            aria-selected={index === activeSuggestion}
            class:active={index === activeSuggestion}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => chooseSuggestion(hit)}
          >
            <span>{hit.name}</span><code>{hit.id64}</code>
          </button>
        {/each}
      {/if}
    </div>
  {/if}
</div>
