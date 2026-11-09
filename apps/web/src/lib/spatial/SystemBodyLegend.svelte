<script lang="ts">
  import type { BodyVisualDescriptor } from './contracts';
  import { bodyFamilyPresentation } from './system-scene';

  let { bodies } = $props<{ bodies: readonly BodyVisualDescriptor[] }>();

  const entries = $derived.by(() => {
    const seen: string[] = [];
    return bodies.flatMap((body: BodyVisualDescriptor) => {
      const family = body.bodyFamily;
      if (!family) return [];
      const key =
        'variant' in family
          ? `${family.family}-${family.variant}`
          : family.family;
      if (seen.includes(key)) return [];
      seen.push(key);
      const presentation = bodyFamilyPresentation(family);
      return [
        {
          key,
          label: presentation.label,
          colour: `rgb(${presentation.colour.map((channel) => Math.round(channel * 255)).join(' ')})`,
          unknown: family.family === 'unknown',
        },
      ];
    });
  });
</script>

{#if entries.length > 0}
  <ul class="body-family-legend" aria-label="Planet class colour legend">
    {#each entries as entry (entry.key)}
      <li data-body-family={entry.key}>
        <span
          class="swatch"
          class:unknown={entry.unknown}
          style:background-color={entry.colour}
          aria-hidden="true"
        ></span>
        {entry.label}
      </li>
    {/each}
  </ul>
{/if}

<style>
  .body-family-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem 0.9rem;
    margin: 0;
    padding: 0.65rem 1rem;
    list-style: none;
    color: #b3c5d2;
    font-size: 0.72rem;
  }
  li {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .swatch {
    width: 0.7rem;
    height: 0.7rem;
    border: 1px solid #ffffff40;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .swatch.unknown {
    opacity: 0.6;
    outline: 1px dashed #b3c5d2;
    outline-offset: 2px;
  }
</style>
