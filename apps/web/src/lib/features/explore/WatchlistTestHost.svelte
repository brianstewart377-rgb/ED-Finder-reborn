<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import { providePersistenceContext } from '$lib/persistence/context';
  import WatchlistPanel from './WatchlistPanel.svelte';

  providePersistenceContext();
</script>

<QueryClientProvider client={queryClient}
  ><WatchlistPanel /></QueryClientProvider
>
