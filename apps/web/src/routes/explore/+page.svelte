<script lang="ts">
  import { onDestroy } from 'svelte';
  import { queryClient } from '$lib/api/query';
  import { page } from '$app/state';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { usePersistenceContext } from '$lib/persistence/context';
  import { ExploreResultCache } from '$lib/features/explore/explore-result-cache';
  import { ExploreViewState } from '$lib/features/explore/explore-view-state.svelte';
  import ExploreWorkspace from '$lib/features/explore/ExploreWorkspace.svelte';
  const viewState = new ExploreViewState(
    () => page,
    FINDER_F4_ENABLED,
    usePersistenceContext(),
  );
  const resultCache = new ExploreResultCache(queryClient);
  onDestroy(() => resultCache.dispose());
</script>

<ExploreWorkspace {viewState} {resultCache} />
