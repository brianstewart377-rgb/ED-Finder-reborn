<script lang="ts">
  import { auth } from '$lib/auth/auth';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import AccountIdentityPanel from '$lib/components/AccountIdentityPanel.svelte';
  import JournalAccountPanel from '$lib/components/JournalAccountPanel.svelte';
</script>

<svelte:head><title>Commander account — ED-Finder</title></svelte:head>
<WorkspaceHeader />
<main class="product-page account-page">
  <div class="product-intro">
    <div>
      <p class="eyebrow">Your commander</p>
      <h1>Account</h1>
    </div>
    <p>Manage the Frontier identities you use to sign in to ED-Finder.</p>
  </div>
  {#if $auth.loading}
    <p role="status">Loading your account…</p>
  {:else if $auth.authenticated}
    {#key $auth.user?.account_id}
      <AccountIdentityPanel />
      <JournalAccountPanel />
    {/key}
  {:else if $auth.error}
    <p role="status">
      We couldn’t check your session. Use Retry above to try again.
    </p>
  {:else}
    <section class="identity-panel">
      <h2>Sign in to manage your account</h2>
      <p class="state-copy">
        Use the sign-in button above to continue securely with Frontier. You can
        browse Explore and Inspect without signing in.
      </p>
    </section>
  {/if}
</main>
