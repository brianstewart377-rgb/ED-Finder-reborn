<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import type { ExploreSystem } from '$lib/api/client';
  import { providePersistenceContext } from '$lib/persistence/context';
  import SystemActions from './SystemActions.svelte';
  let { system } = $props<{ system: ExploreSystem }>();
  providePersistenceContext();
</script>

<QueryClientProvider client={queryClient}
  ><SystemActions {system} /></QueryClientProvider
>
