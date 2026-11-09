<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { Compass } from '@lucide/svelte';
  import AccountControls from './AccountControls.svelte';

  const links = [
    { href: '/explore', label: 'Explore' },
    { href: '/inspect', label: 'Inspect' },
    { href: '/plan', label: 'Plan' },
  ] as const;
</script>

<header class="workspace-header">
  <a class="brand" href={resolve('/')} aria-label="ED-Finder V3 home">
    <span class="brand-mark"
      ><Compass size={25} strokeWidth={1.5} aria-hidden="true" /></span
    >
    <span class="brand-name"
      ><strong>ED:</strong>FINDER<small>ELITE DANGEROUS COMPANION</small></span
    >
  </a>
  <nav aria-label="Product journey">
    {#each links as link (link.href)}
      <a
        href={resolve(link.href)}
        aria-current={page.url.pathname === link.href ? 'page' : undefined}
        >{link.label}</a
      >
    {/each}
  </nav>
  <AccountControls />
</header>
