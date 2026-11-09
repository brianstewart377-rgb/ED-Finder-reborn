<script lang="ts">
  import { QueryClientProvider } from '@tanstack/svelte-query';
  import { queryClient } from '$lib/api/query';
  import { parseId64 } from '$lib/domain/id64';
  import PlanWorkspace from './PlanWorkspace.svelte';
  import type { PlanTab } from './tabs';

  let {
    activeTab = 'system-layout',
    ontabchange,
  }: {
    activeTab?: PlanTab;
    ontabchange?: (tab: PlanTab) => void;
  } = $props();
</script>

<QueryClientProvider client={queryClient}>
  <PlanWorkspace systemId={parseId64('42')} {activeTab} {ontabchange} />
</QueryClientProvider>
