<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { getGalaxyImpact, type GalaxyImpactSummary } from '$lib/api/client';
  import { queryKeys } from '$lib/api/query';

  let { accountId }: { accountId: string | undefined } = $props();
  const componentId = $props.id();
  const headingId = `${componentId}-impact-title`;
  const numbers = new Intl.NumberFormat('en-GB');
  const notableFinds = [
    ['earth_like_worlds', 'Earth-Like Worlds'],
    ['water_worlds', 'Water Worlds'],
    ['ammonia_worlds', 'Ammonia Worlds'],
    ['terraformable_candidates', 'Terraformable candidates'],
    ['gas_giants', 'Gas giants'],
  ] as const satisfies ReadonlyArray<
    readonly [keyof GalaxyImpactSummary, string]
  >;

  const impact = createQuery(() => ({
    queryKey: queryKeys.galaxyImpact(accountId ?? ''),
    queryFn: ({ signal }) => getGalaxyImpact(signal),
    enabled: Boolean(accountId),
    // Personal totals should leave memory when their account panel is removed.
    gcTime: 0,
  }));
</script>

<section class="identity-panel" aria-labelledby={headingId}>
  <h2 id={headingId}>Your Galaxy Impact</h2>
  {#if !accountId}
    <p>Sign in to see your exploration totals.</p>
  {:else if impact.isPending}
    <p role="status">Loading your impact…</p>
  {:else if impact.isError}
    <p role="alert">Your impact could not be loaded. Please try again.</p>
    <button
      class="quiet-button"
      type="button"
      onclick={() => void impact.refetch()}>Try again</button
    >
  {:else}
    <p class="identity-panel-copy">
      Across your imported journals, you've recorded:
    </p>
    {#if impact.data.bodies_scanned === 0}
      <p>
        Import your journals to start your exploration record. Sharing is
        optional.
      </p>
    {/if}
    <dl class="impact-totals">
      <div>
        <dt>Systems discovered</dt>
        <dd>{numbers.format(impact.data.systems_discovered)}</dd>
      </div>
      <div>
        <dt>Bodies scanned</dt>
        <dd>{numbers.format(impact.data.bodies_scanned)}</dd>
      </div>
    </dl>
    <h3>Notable finds</h3>
    <dl class="impact-finds">
      {#each notableFinds as [key, label] (key)}
        <div>
          <dt>{label}</dt>
          <dd>{numbers.format(impact.data[key])}</dd>
        </div>
      {/each}
    </dl>
    <p class="identity-panel-note">
      Totals cover the imported scans of your linked commanders, including stars
      and planets. Systems discovered means systems you've scanned, not
      first-discovery credit. Terraformable candidates can also appear among the
      other finds. Sharing does not change these totals.
    </p>
  {/if}
</section>

<style>
  .impact-totals,
  .impact-finds {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 11rem), 1fr));
    gap: 1rem;
    margin-block: 1.5rem;
  }
  .impact-totals div,
  .impact-finds div {
    padding: 1rem;
    border: 1px solid var(--line);
    background: var(--panel);
    background-image: var(--panel-gloss);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-panel);
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0.5rem 0 0;
    font-size: 1.75rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .impact-totals dd {
    font-size: clamp(2rem, 5vw, 3rem);
  }
  h3 {
    margin-block: 1.5rem 0;
  }
  .identity-panel-note {
    max-width: 65ch;
    line-height: 1.6;
  }
</style>
