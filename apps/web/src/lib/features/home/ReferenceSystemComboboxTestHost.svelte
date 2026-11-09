<script lang="ts">
  import {
    QueryClientProvider,
    type QueryClient,
  } from '@tanstack/svelte-query';
  import type { AutocompleteSystem } from '$lib/api/client';
  import ReferenceSystemCombobox from './ReferenceSystemCombobox.svelte';

  let {
    client,
    invalid = false,
    describedBy,
    onsubmit,
  }: {
    client: QueryClient;
    invalid?: boolean;
    describedBy?: string;
    onsubmit?: () => void;
  } = $props();
  let selected = $state<AutocompleteSystem | null>(null);
  let combobox: ReturnType<typeof ReferenceSystemCombobox>;
</script>

<QueryClientProvider {client}>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      onsubmit?.();
    }}
  >
    <ReferenceSystemCombobox
      bind:this={combobox}
      bind:selected
      {invalid}
      {describedBy}
    />
    <button type="submit">Submit</button>
    <button type="button" onclick={() => combobox.focus()}>Focus field</button>
  </form>
  <output aria-label="Selected system">{selected?.id64 ?? 'None'}</output>
</QueryClientProvider>
