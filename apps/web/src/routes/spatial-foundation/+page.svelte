<script lang="ts">
  import { resolve } from '$app/paths';
  import SpatialCanvas from '$lib/spatial/SpatialCanvas.svelte';
</script>

<svelte:head>
  <title>Spatial runtime foundation — ED-Finder V3</title>
  <meta
    name="description"
    content="Non-product ED-Finder V3 spatial runtime diagnostic"
  />
</svelte:head>

<main class="spatial-foundation">
  <a href={resolve('/')}>← ED-Finder V3</a>
  <header>
    <p class="eyebrow">NON-PRODUCT diagnostic</p>
    <h1>Spatial runtime foundation</h1>
    <p>
      This isolated surface proves renderer startup and lifecycle only. It is
      not Finder, Inspect, or the V3 map design.
    </p>
  </header>
  <SpatialCanvas />
</main>

<style>
  .spatial-foundation {
    padding: 2rem 0 4rem;
  }

  header {
    max-width: 48rem;
    margin: 3rem 0 2rem;
  }

  h1 {
    margin-bottom: 1.25rem;
  }

  header p:last-child {
    color: #aeb9c6;
    line-height: 1.6;
  }
</style>
