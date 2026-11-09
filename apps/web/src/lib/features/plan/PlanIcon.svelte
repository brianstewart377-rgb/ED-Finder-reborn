<script lang="ts">
  import type { PlanStepV1 } from '$lib/api';
  import type { PlannerSite } from './model';

  let {
    kind,
  }: {
    kind: PlannerSite['status'] | PlanStepV1['result'] | 'orbital' | 'surface';
  } = $props();
</script>

<svg
  class="plan-icon"
  viewBox="0 0 16 16"
  width="14"
  height="14"
  fill="none"
  stroke="currentColor"
  stroke-width="1.5"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  focusable="false"
>
  {#if kind === 'orbital'}
    <circle cx="8" cy="8" r="5.5" stroke-dasharray="0.5 3" />
  {:else if kind === 'surface'}
    <circle cx="8" cy="8" r="5.5" />
    <ellipse cx="8" cy="8" rx="2.5" ry="5.5" />
    <path d="M2.5 8h11" />
  {:else if kind === 'complete' || kind === 'COUNTED'}
    <circle cx="8" cy="8" r="5.5" fill="currentColor" stroke="none" />
  {:else if kind === 'building'}
    <circle cx="8" cy="8" r="5.5" />
    <path d="M8 2.5a5.5 5.5 0 0 0 0 11Z" fill="currentColor" stroke="none" />
  {:else if kind === 'planned'}
    <circle cx="8" cy="8" r="5.5" />
  {:else if kind === 'demolished' || kind === 'BLOCKED'}
    <path d="m4 4 8 8M12 4l-8 8" />
  {:else if kind === 'OK'}
    <path d="m3 8 3 3 7-7" />
  {:else if kind === 'NEEDS_INFO'}
    <circle cx="8" cy="8" r="6" />
    <path d="M6.2 5.8a1.8 1.8 0 1 1 3.1 1.3C8.5 7.8 8 8 8 9" />
    <circle cx="8" cy="11.5" r="0.7" fill="currentColor" stroke="none" />
  {:else if kind === 'IGNORED'}
    <path d="M3 8h10" />
  {/if}
</svg>

<style>
  .plan-icon {
    display: inline-block;
    flex: 0 0 0.875rem;
    width: 0.875rem;
    height: 0.875rem;
    vertical-align: -0.125rem;
  }
</style>
