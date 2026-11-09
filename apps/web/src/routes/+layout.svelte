<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import AppShell from '$lib/components/AppShell.svelte';
  import { queryClient } from '$lib/api/query';
  import { providePersistenceContext } from '$lib/persistence/context';
  import '../app.css';

  let { children } = $props();
  providePersistenceContext();
</script>

<QueryClientProvider client={queryClient}>
  <AppShell>{@render children()}</AppShell>
</QueryClientProvider>
