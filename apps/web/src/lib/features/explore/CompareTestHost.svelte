<script lang="ts">
  import type { Id64 } from '$lib/domain/id64';
  import { providePersistenceContext } from '$lib/persistence/context';
  import ComparePanel from './ComparePanel.svelte';

  let { onOpenDetail } = $props<{ onOpenDetail?: (id64: Id64) => void }>();
  providePersistenceContext();
</script>

<ComparePanel {onOpenDetail} />
