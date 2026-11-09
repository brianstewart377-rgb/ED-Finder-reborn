<script lang="ts">
  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
  import { onMount } from 'svelte';
  import { auth } from '$lib/auth/auth';
  import Page from '../../routes/+page.svelte';
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  onMount(() => void auth.bootstrap());
</script>

<QueryClientProvider {client}><Page /></QueryClientProvider>
