<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { onDestroy } from 'svelte';
  import { get } from 'svelte/store';
  import { ApiError } from '$lib/api/client';
  import { refreshCompareSnapshot } from '$lib/api/compare';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import type { Id64 } from '$lib/domain/id64';
  import { usePersistenceContext } from '$lib/persistence/context';
  import type { CompareEntry } from '$lib/persistence/storage';
  import {
    buildCompareNotes,
    buildCompareRows,
    compareCsv,
    COMPARE_MAX,
    snapshotProvenanceSummary,
    snapshotScoreSummary,
  } from './compare-metrics';
  import { inspectHref, navigateWithAnchor } from './navigation';

  let { onOpenDetail } = $props<{ onOpenDetail?: (id64: Id64) => void }>();
  const { compare } = usePersistenceContext();
  const entries = $derived($compare.value);
  const rows = $derived(
    FINDER_F4_ENABLED
      ? buildCompareRows(entries, { finderF4Enabled: true })
      : buildCompareRows(entries),
  );
  const notes = $derived(
    FINDER_F4_ENABLED
      ? buildCompareNotes(entries, { finderF4Enabled: true })
      : [],
  );
  const componentId = $props.id();
  const headingId = `${componentId}-compare-title`;
  let refreshing = $state(false);
  let refreshStatus = $state('');
  let refreshFailures = $state<Record<string, 'unknown' | 'unavailable'>>({});
  let refreshController: AbortController | null = null;
  onDestroy(() => refreshController?.abort());

  async function refresh(candidates: readonly CompareEntry[]): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    refreshStatus = 'Refreshing comparison…';
    const controller = new AbortController();
    refreshController = controller;
    const requested = candidates.slice(0, COMPARE_MAX);
    const results = await Promise.allSettled(
      requested.map((entry) =>
        refreshCompareSnapshot(entry, controller.signal),
      ),
    );
    if (controller.signal.aborted) return;
    const replacements: { original: CompareEntry; value: CompareEntry }[] = [];
    const failures = { ...refreshFailures };
    const currentEntries = get(compare).value;
    results.forEach((result, index) => {
      const original = requested[index];
      if (!currentEntries.includes(original)) return;
      if (result.status === 'fulfilled') {
        replacements.push({ original, value: result.value });
        delete failures[original.id64];
      } else {
        failures[original.id64] =
          result.reason instanceof ApiError &&
          (result.reason.status === 404 || result.reason.status === 422)
            ? 'unknown'
            : 'unavailable';
      }
    });
    // Use the current list so removal, clearing or a newer saved snapshot while
    // a request is in flight cannot be undone by its response.
    if (replacements.length > 0)
      compare.set(
        get(compare).value.map(
          (entry) =>
            replacements.find(({ original }) => original === entry)?.value ??
            entry,
        ),
      );
    refreshFailures = failures;
    refreshStatus = results.every((result) => result.status === 'fulfilled')
      ? 'Comparison refreshed.'
      : 'Some systems could not be refreshed. Their saved values are retained.';
    refreshing = false;
    refreshController = null;
  }

  function remove(id64: Id64): void {
    compare.set(entries.filter((entry) => entry.id64 !== id64));
    const failures = { ...refreshFailures };
    delete failures[id64];
    refreshFailures = failures;
  }

  function clear(): void {
    if (
      window.confirm(`Clear all ${entries.length} systems from comparison?`)
    ) {
      compare.set([]);
      refreshFailures = {};
    }
  }

  function exportCsv(): void {
    const blob = new Blob(
      [
        FINDER_F4_ENABLED
          ? compareCsv(entries, { finderF4Enabled: true })
          : compareCsv(entries),
      ],
      { type: 'text/csv;charset=utf-8' },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `ed-compare-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function inspectLink(id64: Id64): string {
    return FINDER_F4_ENABLED && page.url.pathname === resolve('/explore')
      ? inspectHref(id64, page.url)
      : resolve(`/inspect?system=${id64}`);
  }

  function openInspect(event: MouseEvent, id64: Id64): void {
    if (
      !FINDER_F4_ENABLED ||
      page.url.pathname !== resolve('/explore') ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    void navigateWithAnchor(inspectLink(id64));
  }
</script>

<section class="compare-panel" aria-labelledby={headingId}>
  <header class="compare-heading">
    <div>
      <h2 id={headingId}>Compare systems</h2>
      <p>Up to {COMPARE_MAX} candidates side by side.</p>
    </div>
    <span class="compare-count" aria-live="polite"
      >{entries.length} / {COMPARE_MAX} systems</span
    >
    {#if entries.length > 0}
      <div class="compare-actions">
        <button
          type="button"
          class="secondary-button"
          disabled={refreshing}
          onclick={() => void refresh(entries)}>Refresh</button
        >
        <button type="button" class="secondary-button" onclick={exportCsv}
          >Export CSV</button
        >
        <button type="button" class="secondary-button" onclick={clear}
          >Clear comparison</button
        >
      </div>
    {/if}
  </header>

  {#if $compare.diagnostic}
    <p role="alert">
      Your comparison could not be saved or restored in this browser. Changes
      may not survive a reload.
    </p>
  {/if}

  {#if refreshStatus}<p role="status">{refreshStatus}</p>{/if}

  {#if entries.length === 0}
    <div class="compare-empty">
      <h3>No systems added</h3>
      <p>
        Choose Compare on at least two systems in Finder, Watchlist or Pinned to
        see their attributes side by side.
      </p>
    </div>
  {:else}
    {#if entries.length === 1}
      <p role="status">
        Add one more system to compare your candidates side by side.
      </p>
    {/if}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll the wide comparison table.) -->
    <div
      class="compare-scroll"
      role="region"
      aria-label="Comparison metrics"
      tabindex="0"
    >
      <table>
        <caption>System comparison</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            {#each entries as entry (entry.id64)}
              <th scope="col">
                {#if onOpenDetail}
                  <button
                    type="button"
                    class="system-name"
                    onclick={() => onOpenDetail?.(entry.id64)}
                    >{entry.name}</button
                  >
                {:else}
                  <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectLink resolves both legacy and state-preserving destinations -->
                  <a
                    class="system-name"
                    href={FINDER_F4_ENABLED
                      ? inspectLink(entry.id64)
                      : resolve(`/inspect?system=${entry.id64}`)}
                    onclick={FINDER_F4_ENABLED
                      ? (event) => openInspect(event, entry.id64)
                      : undefined}>{entry.name}</a
                  >
                  <!-- eslint-enable svelte/no-navigation-without-resolve -->
                {/if}
                {#if refreshFailures[entry.id64]}
                  <span
                    class="snapshot-detail"
                    data-unavailable-fields
                    data-availability={refreshFailures[entry.id64]}
                    role="status"
                  >
                    {#if refreshFailures[entry.id64] === 'unknown'}
                      Unknown — the published source has no current value for
                      this system. Saved values are retained.
                    {:else}
                      Temporarily unavailable — refresh failed. Saved values are
                      retained.
                      <button
                        type="button"
                        class="secondary-button"
                        disabled={refreshing}
                        aria-label={`Retry ${entry.name}`}
                        onclick={() => void refresh([entry])}>Retry</button
                      >
                    {/if}
                  </span>
                {/if}
                {#if FINDER_F4_ENABLED}
                  <span class="snapshot-detail"
                    >{snapshotScoreSummary(entry)}</span
                  >
                  {#if snapshotProvenanceSummary(entry)}
                    <span class="snapshot-detail"
                      >{snapshotProvenanceSummary(entry)}</span
                    >
                  {/if}
                {/if}
                <button
                  type="button"
                  class="remove-system"
                  aria-label={`Remove ${entry.name} from comparison`}
                  onclick={() => remove(entry.id64)}>Remove</button
                >
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.label)}
            <tr>
              <th scope="row">{row.label}</th>
              {#each row.cells as cell, index (entries[index].id64)}
                <td class:best={cell.best}>
                  {cell.display}
                  {#if cell.best}<span class="best-value">Best value</span>{/if}
                </td>
              {/each}
            </tr>
          {/each}
          <tr>
            <th scope="row">Links</th>
            {#each entries as entry (entry.id64)}
              <td class="external-links">
                <a
                  href={`https://spansh.co.uk/system/${entry.id64}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`${entry.name} on Spansh`}>Spansh ↗</a
                >
                <a
                  href={`https://inara.cz/starsystem/?search=${encodeURIComponent(entry.name)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`${entry.name} on Inara`}>Inara ↗</a
                >
              </td>
            {/each}
          </tr>
        </tbody>
      </table>
    </div>
    {#each notes as note (note)}
      <p class="compare-note">{note}</p>
    {/each}
    <p class="compare-note">
      Best values are labelled where at least two known values differ. Shorter
      distances are better; higher scores and counts are better.
    </p>
  {/if}
  <p class="compare-note">
    Saved in this browser. Values reflect the capture time shown. Refresh
    explicitly loads the current published Finder values. Search reference
    distances retain their own capture time when the reference cannot be
    refreshed.
  </p>
</section>

<style>
  .compare-panel {
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: 0.75rem;
    background: var(--color-panel);
    padding: 1.1rem;
  }
  .compare-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .compare-heading h2 {
    margin: 0;
    font-size: 1.2rem;
  }
  .compare-heading p {
    margin: 0.4rem 0 0;
    color: var(--muted);
  }
  .compare-count {
    margin-left: auto;
    font-variant-numeric: tabular-nums;
  }
  .compare-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .compare-empty {
    padding: 1.5rem 0;
  }
  .compare-empty h3 {
    margin-top: 0;
  }
  .compare-empty p,
  .compare-note {
    color: var(--muted);
    line-height: 1.6;
  }
  .snapshot-detail {
    display: block;
    max-width: 24rem;
    margin-top: 0.5rem;
    color: var(--muted);
    font-size: 0.8rem;
    font-weight: 400;
    overflow-wrap: anywhere;
  }
  .compare-note {
    font-size: 0.82rem;
    margin-bottom: 0;
  }
  .compare-scroll {
    max-width: 100%;
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 0.4rem;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
  }
  caption {
    padding: 0.7rem;
    text-align: left;
    color: var(--muted);
  }
  th,
  td {
    padding: 0.8rem;
    text-align: left;
    border-top: 1px solid var(--line);
    vertical-align: top;
  }
  thead th {
    min-width: 12rem;
    background: var(--panel-raised);
  }
  th:first-child {
    position: sticky;
    left: 0;
    background: var(--color-panel);
    z-index: 1;
    min-width: 10rem;
  }
  tbody th {
    font-weight: 500;
  }
  .system-name,
  .remove-system {
    background: transparent;
    border: 0;
    color: inherit;
    font: inherit;
    cursor: pointer;
    text-align: left;
    padding: 0;
    text-decoration: underline;
    text-underline-offset: 0.2em;
  }
  .system-name {
    font-weight: 700;
  }
  .remove-system {
    display: block;
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 0.7rem;
  }
  .best {
    font-weight: 700;
  }
  .best-value {
    display: block;
    width: fit-content;
    margin-top: 0.4rem;
    border: 1px solid currentColor;
    border-radius: 0.2rem;
    padding: 0.15rem 0.35rem;
    font-size: 0.7rem;
  }
  .external-links a {
    display: inline-block;
    margin: 0 0.7rem 0.3rem 0;
  }
</style>
