<script lang="ts">
  import { page } from '$app/state';
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { onDestroy, untrack } from 'svelte';
  import { queryClient } from '$lib/api/query';
  import { providePersistenceContext } from '$lib/persistence/context';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { ExploreResultCache } from '$lib/features/explore/explore-result-cache';
  import { ExploreViewState } from './explore-view-state.svelte';
  import ExploreWorkspace from './ExploreWorkspace.svelte';

  type TestPage = { url: URL; state: App.PageState };
  let {
    initialPage,
    installPage,
    viewState: suppliedViewState,
    mounted = true,
  } = $props<{
    initialPage?: TestPage;
    installPage?: (page: TestPage) => void;
    viewState?: ExploreViewState;
    mounted?: boolean;
  }>();
  const reactivePage = $state(
    untrack(
      () =>
        initialPage ?? { url: new URL('http://localhost/explore'), state: {} },
    ),
  );
  untrack(() => installPage?.(reactivePage));

  const persistence = providePersistenceContext();
  const viewState = untrack(
    () =>
      suppliedViewState ??
      new ExploreViewState(() => page, FINDER_F4_ENABLED, persistence),
  );
  const resultCache = new ExploreResultCache(queryClient);
  onDestroy(() => resultCache.dispose());
</script>

<QueryClientProvider client={queryClient}
  >{#if mounted}<ExploreWorkspace
      {viewState}
      {resultCache}
    />{/if}</QueryClientProvider
>
