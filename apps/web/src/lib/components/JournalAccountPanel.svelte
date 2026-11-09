<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';
  import { useQueryClient } from '@tanstack/svelte-query';
  import { auth } from '$lib/auth/auth';
  import { queryKeys } from '$lib/api/query';
  import GalaxyImpactPanel from './GalaxyImpactPanel.svelte';
  import {
    getVerifiedCommanders,
    importVerifiedJournals,
    offerGalaxyFacts,
    getGalaxyContributions,
    withdrawGalaxyContribution,
    type VerifiedCommanderResponse,
    type V3VerifiedImportReceipt,
    type ContributionRow,
  } from '$lib/api/client';
  import { streamJournals } from '$lib/journal/parse';
  import {
    uploadJournalBatches,
    type CommittedBatch,
  } from '$lib/journal/uploader';

  let commanders = $state<VerifiedCommanderResponse[]>([]);
  let selected = $state<FileList | undefined>();
  let sharing = $state(false);
  let busy = $state(false);
  let status = $state('Checking linked commanders…');
  let error = $state('');
  let receipt = $state<V3VerifiedImportReceipt | null>(null);
  let warnings = $state<Array<{ name: string; reason: string }>>([]);
  let heldSummary = $state('');
  let contributions = $state<ContributionRow[]>([]);
  let page = $state(0);
  let sharingPending = $state<Array<{ id: string; hashes: string[] }>>([]);
  const lifetime = new AbortController();
  let operation = $state<AbortController | null>(null);
  let refreshGeneration = 0;
  const queryClient = useQueryClient();
  const accountId = $derived(
    $auth.authenticated ? $auth.user?.account_id : undefined,
  );
  const sharedDate = new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });

  function sharingStatus(state: string): string {
    switch (state) {
      case 'OFFERED':
        return 'Awaiting review';
      case 'ELIGIBLE':
        return 'Approved for sharing';
      case 'REJECTED':
        return 'Not selected for sharing';
      case 'WITHDRAWN':
        return 'Sharing withdrawn';
      default:
        return 'Review in progress';
    }
  }

  function formatSharedDate(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? 'Date unavailable'
      : sharedDate.format(date);
  }

  // Maps the exact held-reason sentences produced by import-worker.ts to a
  // short clause used to build the "why nothing was imported" summary below.
  // Keep in sync with apps/web/src/lib/journal/import-worker.ts.
  const HELD_REASON_CLAUSES: Record<string, string> = {
    'File exceeds this importer’s per-file size limit':
      'exceed the per-file size limit',
    'Commander identity record has an invalid timestamp; file held for review':
      'have no valid Commander/LoadGame timestamp',
    'No supported events with valid timestamps':
      'have no supported events with valid timestamps',
    'Could not read this file; other files can continue': 'could not be read',
    'Select up to 2000 files per import':
      'exceed the 2000-file selection limit',
  };

  function summarizeHeldFiles(
    held: Array<{ name: string; reason: string }>,
  ): string {
    if (!held.length)
      return 'No recognizable journal files were found in this selection.';
    const counts: Record<string, number> = {};
    for (const item of held) {
      const clause = HELD_REASON_CLAUSES[item.reason] ?? item.reason;
      counts[clause] = (counts[clause] ?? 0) + 1;
    }
    const parts = Object.entries(counts).map(
      ([clause, count]) =>
        `${count} ${count === 1 ? 'file' : 'files'} ${clause}`,
    );
    return `All ${held.length} selected file${held.length === 1 ? '' : 's'} were held: ${parts.join(', ')}.`;
  }

  async function refresh(): Promise<boolean> {
    const generation = ++refreshGeneration;
    error = '';
    try {
      const [linked, rows] = await Promise.allSettled([
        getVerifiedCommanders(lifetime.signal),
        getGalaxyContributions(page * 50, lifetime.signal),
      ]);
      if (lifetime.signal.aborted || generation !== refreshGeneration)
        return false;
      if (linked.status === 'fulfilled') commanders = linked.value;
      if (rows.status === 'fulfilled') contributions = rows.value;
      if (linked.status === 'rejected' || rows.status === 'rejected')
        error = 'Some account data could not be loaded. Retry to refresh it.';
      status = commanders.length
        ? 'Ready to import your journals'
        : 'Sign in again to verify your commander with Frontier';
      return rows.status === 'fulfilled';
    } catch {
      if (!lifetime.signal.aborted && generation === refreshGeneration)
        error = 'Your journal account could not be loaded. Please try again.';
      return false;
    }
  }

  async function shareSavedImports() {
    if (!sharingPending.length) return;
    busy = true;
    error = '';
    operation ??= new AbortController();
    try {
      let offered = 0;
      for (const pending of [...sharingPending]) {
        for (let start = 0; start < pending.hashes.length; start += 200) {
          const result = await offerGalaxyFacts(
            pending.id,
            pending.hashes.slice(start, start + 200),
            operation.signal,
          );
          if (lifetime.signal.aborted) return;
          offered += result.new_offers;
          // Drop only successful chunks; a later failure resumes the remainder.
          const remaining = pending.hashes.slice(start + 200);
          sharingPending = remaining.length
            ? sharingPending.map((item) =>
                item.id === pending.id ? { ...item, hashes: remaining } : item,
              )
            : sharingPending.filter((item) => item.id !== pending.id);
          for (const count of Object.values(result.skipped)) {
            if (count)
              warnings = [
                ...warnings,
                {
                  name: 'Galaxy sharing',
                  reason: `${count} observations could not be shared. Your private journal is saved.`,
                },
              ];
          }
        }
      }
      await refresh();
      if (!lifetime.signal.aborted)
        status = `Journal saved. ${offered} new galaxy observations offered for review`;
    } catch {
      if (!lifetime.signal.aborted)
        error =
          'Your Journal is saved. Sharing needs a retry. Please try again.';
    } finally {
      if (!lifetime.signal.aborted) busy = false;
      operation = null;
    }
  }

  async function upload() {
    if (busy || !selected?.length) return;
    busy = true;
    error = '';
    receipt = null;
    warnings = [];
    heldSummary = '';
    const contributeThisImport = sharing;
    const importingAccountId = accountId;
    operation = new AbortController();
    const committed: CommittedBatch[] = [];
    try {
      status = 'Reading and uploading your journals…';
      const result = await uploadJournalBatches(
        streamJournals(Array.from(selected), operation.signal),
        importVerifiedJournals,
        {
          parserVersion: 'journal-import-worker-v3',
          signal: operation.signal,
          onProgress: (progress) => {
            if (!lifetime.signal.aborted)
              status = `Uploaded ${progress.filesCommitted}/${progress.filesParsed} files · ${progress.eventsCommitted} events · batch ${progress.batchesSent}`;
          },
          onBatchCommitted: (batch) => committed.push(batch),
        },
      );
      if (lifetime.signal.aborted) return;
      warnings = result.held;
      receipt = result.receipt;
      if (operation.signal.aborted) {
        status = 'Import stopped. Saved events remain; re-import to continue.';
        return;
      }
      if (!result.committedShas.length && !result.failed.length) {
        heldSummary = summarizeHeldFiles(result.held);
        status = 'Import held: no files were saved';
        return;
      }
      status = result.failed.length
        ? `Import finished — ${result.failed.length} batch${result.failed.length === 1 ? '' : 'es'} need a retry. Re-import to continue; saved events are skipped.`
        : 'Import finished';
      if (contributeThisImport && committed.length) {
        const pending = new SvelteMap(
          sharingPending.map((item) => [item.id, item.hashes]),
        );
        for (const batch of committed)
          for (const id of batch.importIds)
            pending.set(id, [
              ...new Set([...(pending.get(id) ?? []), ...batch.shas]),
            ]);
        sharingPending = [...pending].map(([id, hashes]) => ({ id, hashes }));
        await shareSavedImports();
      } else {
        await refresh();
        if (!lifetime.signal.aborted && !result.failed.length)
          status = 'Import finished';
      }
    } catch {
      if (!lifetime.signal.aborted)
        error = operation?.signal.aborted
          ? 'Import stopped. Any events already committed remain saved; retrying is safe.'
          : 'Import could not finish. Saved progress is safe; please try again.';
    } finally {
      if (!lifetime.signal.aborted) busy = false;
      operation = null;
      if (committed.length && importingAccountId) {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.galaxyImpact(importingAccountId),
        });
      }
    }
  }

  async function withdraw(id: string) {
    busy = true;
    error = '';
    try {
      await withdrawGalaxyContribution(id, lifetime.signal);
      await refresh();
    } catch {
      if (!lifetime.signal.aborted)
        error = 'Sharing could not be withdrawn. Please try again.';
    } finally {
      if (!lifetime.signal.aborted) busy = false;
    }
  }

  async function changePage(delta: number) {
    // Serialize page changes: without this, overlapping next/prev clicks each
    // capture a different `previous`, and a stale call's rollback could clobber
    // a newer page that loaded successfully. `busy` also disables the paging
    // buttons for the duration, so only one change is ever in flight.
    if (busy) return;
    const previous = page;
    page += delta;
    busy = true;
    try {
      // Keep page aligned with the rows actually displayed: if the new page
      // fails to load, refresh() leaves the prior contributions in place, so
      // revert page too or the labels/withdraw buttons relabel stale rows.
      if (!(await refresh())) page = previous;
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    void refresh();
  });
  onDestroy(() => {
    lifetime.abort();
    operation?.abort();
  });
</script>

<section class="identity-panel" aria-labelledby="journal-account-title">
  <p class="eyebrow">Your exploration</p>
  <h2 id="journal-account-title">Commanders and journals</h2>
  {#if commanders.length}
    <ul>
      {#each commanders as commander (commander.commander_id)}
        <li>
          {commander.commander_name} · {commander.journal_fid} · Verified with Frontier
        </li>
      {/each}
    </ul>
  {:else}
    <p>
      Re-authenticate to link your journal identity. A commander name alone is
      not used to match logs.
    </p>
    <button class="secondary-button" type="button" onclick={() => auth.signIn()}
      >Verify with Frontier</button
    >
  {/if}
  <p>
    Files from unlinked commanders are held for review while your valid files
    continue. Files with multiple commanders are held intact.
  </p>
  <div class="journal-file-select">
    <input
      id="journal-files"
      class="journal-file-input"
      type="file"
      multiple
      accept=".log,.jsonl"
      bind:files={selected}
      disabled={busy}
    />
    <label for="journal-files" class="secondary-button" class:is-disabled={busy}
      >Select journal logs</label
    >
    <span class="state-copy journal-file-status" aria-live="polite">
      {selected?.length
        ? `${selected.length} file${selected.length === 1 ? '' : 's'} selected`
        : 'No files selected'}
    </span>
  </div>
  <p class="state-copy">
    Up to 2000 files, 32 MiB per file. Large selections upload in batches and
    resume safely if interrupted.
  </p>
  <label class="journal-sharing-choice">
    <input type="checkbox" bind:checked={sharing} disabled={busy} />
    Share eligible discoveries on ED-Finder and with apps that use its galaxy data
  </label>
  <p class="state-copy">
    Optional and separate from research sharing. Names, credits and your private
    history stay private. Shared observations need review before publication.
    Withdrawal stops future use; discoveries already published may remain until
    the galaxy catalogue is updated.
  </p>
  <div class="identity-panel-heading">
    <button
      class="primary-button"
      type="button"
      disabled={busy || !selected?.length}
      onclick={() => void upload()}>Import journals</button
    >
    {#if busy && operation}<button
        class="quiet-button"
        type="button"
        onclick={() => operation?.abort()}>Stop import</button
      >{/if}
    {#if sharingPending.length && !busy}<button
        class="secondary-button"
        type="button"
        onclick={() => void shareSavedImports()}
        >Retry sharing saved import</button
      >{/if}
  </div>
  <p role="status">{status}</p>
  {#if heldSummary}<p role="alert">{heldSummary}</p>{/if}
  {#if error}<p role="alert">{error}</p>
    <button
      type="button"
      class="quiet-button"
      disabled={busy}
      onclick={() => void refresh()}>Refresh account</button
    >
  {/if}
  {#if receipt}
    <p>
      {receipt.events_inserted} events saved · {receipt.duplicates_skipped} duplicate
      events · {receipt.files_skipped} previously imported files
    </p>
    <ul>
      {#each receipt.held_files as file, index (index)}<li>
          {file.name}: {file.reason.replaceAll('_', ' ')}
        </li>{/each}
    </ul>
  {/if}
  {#if warnings.length}<ul>
      {#each warnings as warning, index (index)}<li>
          {warning.name}: {warning.reason}
        </li>{/each}
    </ul>{/if}
</section>

<GalaxyImpactPanel {accountId} />

<details class="identity-panel galaxy-sharing">
  <summary>Manage galaxy sharing</summary>
  {#if !contributions.length}<p>
      No observations shared on this page. Sharing is optional and does not
      change your impact totals.
    </p>{/if}
  <ul>
    {#each contributions as contribution, index (contribution.contribution_id)}
      <li>
        Observation {page * 50 + index + 1} · Shared on {formatSharedDate(
          contribution.offered_at,
        )}
        · {sharingStatus(contribution.contribution_state)}
        {#if contribution.used_in_generation}
          · Prepared for a galaxy update{/if}
        {#if contribution.contribution_state !== 'WITHDRAWN'}
          <button
            class="quiet-button"
            type="button"
            aria-label={`Withdraw sharing for observation ${page * 50 + index + 1}`}
            disabled={busy}
            onclick={() => void withdraw(contribution.contribution_id)}
            >Withdraw sharing</button
          >
        {/if}
      </li>
    {/each}
  </ul>
  <button
    class="quiet-button"
    type="button"
    disabled={busy || page === 0}
    onclick={() => void changePage(-1)}>Previous</button
  >
  <button
    class="quiet-button"
    type="button"
    disabled={busy || contributions.length < 50}
    onclick={() => void changePage(1)}>Next</button
  >
</details>

<style>
  .galaxy-sharing summary {
    cursor: pointer;
    font-size: 1.25rem;
    font-weight: 700;
  }
  .journal-file-select {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin: 0.25rem 0 0.5rem;
  }
  /* Keep the native file input accessible (focusable + screen-reader labelled)
     while the styled <label> is the visible button. Not display:none, so
     keyboard users can Tab to it and press Enter/Space to open the dialog. */
  .journal-file-input {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }
  label.secondary-button {
    cursor: pointer;
  }
  label.secondary-button.is-disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .journal-file-input:focus-visible + label.secondary-button {
    outline: 2px solid var(--color-cyan, #4dd0e1);
    outline-offset: 2px;
  }
  .journal-file-status {
    margin: 0;
  }
</style>
