<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { untrack } from 'svelte';
  import { queryClient } from '$lib/api/query';
  import PlanPage from './+page.svelte';

  type TestPage = { url: URL; state: App.PageState };
  let { initialPage, installPage } = $props<{
    initialPage: TestPage;
    installPage: (page: TestPage) => void;
  }>();
  const reactivePage = $state(untrack(() => initialPage));
  untrack(() => installPage(reactivePage));
</script>

<QueryClientProvider client={queryClient}><PlanPage /></QueryClientProvider>
