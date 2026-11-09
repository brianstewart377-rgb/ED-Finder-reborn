<script lang="ts">
  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
  import JournalAccountPanel from './JournalAccountPanel.svelte';

  let {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    }),
  } = $props<{ client?: QueryClient }>();
</script>

<QueryClientProvider {client}><JournalAccountPanel /></QueryClientProvider>
