<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import type { InspectTab } from './inspect-tabs';
  import SystemDetail from './SystemDetail.svelte';

  let { id64, tab } = $props<{ id64: Id64; tab?: InspectTab }>();
</script>

<QueryClientProvider client={queryClient}
  ><SystemDetail {id64} {tab} /></QueryClientProvider
>
