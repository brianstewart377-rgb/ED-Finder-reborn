<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { untrack } from 'svelte';
  import { queryClient } from '$lib/api/query';
  import { providePersistenceContext } from '$lib/persistence/context';
  import AppShell from './AppShell.svelte';

  type TestPage = { url: URL; state: App.PageState };
  let { initialPage, installPage } = $props<{
    initialPage: TestPage;
    installPage: (page: TestPage) => void;
  }>();
  const reactivePage = $state(untrack(() => initialPage));
  untrack(() => installPage(reactivePage));
  providePersistenceContext();
</script>

<QueryClientProvider client={queryClient}>
  <AppShell><p>Shell content</p></AppShell>
</QueryClientProvider>
