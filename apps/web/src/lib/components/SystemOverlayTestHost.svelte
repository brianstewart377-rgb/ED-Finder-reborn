<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import type { Id64 } from '$lib/domain/id64';
  import SystemOverlay from './SystemOverlay.svelte';

  let { id64 } = $props<{ id64: Id64 }>();
</script>

<QueryClientProvider client={queryClient}
  ><SystemOverlay {id64} /></QueryClientProvider
>
