<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import ColonyPotential from './ColonyPotential.svelte';

  let {
    id64,
    active = true,
    loadedBodyCount = null,
    sourceBodyCount = null,
  } = $props<{
    id64: Id64;
    active?: boolean;
    loadedBodyCount?: number | null;
    sourceBodyCount?: number | null;
  }>();
</script>

<QueryClientProvider client={queryClient}
  ><ColonyPotential
    {id64}
    headingId="test"
    {active}
    {loadedBodyCount}
    {sourceBodyCount}
  /></QueryClientProvider
>
