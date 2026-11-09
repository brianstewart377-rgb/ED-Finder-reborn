<script lang="ts">
  import { goto } from '$app/navigation';
  import { asset, resolve } from '$app/paths';
  import { ArrowUpRight } from '@lucide/svelte';
  import type { AutocompleteSystem } from '$lib/api/client';
  import BootstrapStatus from '$lib/components/BootstrapStatus.svelte';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { ANY_ARCHETYPE, ARCHETYPES } from '$lib/features/explore/archetypes';
  import ReferenceSystemCombobox from '$lib/features/home/ReferenceSystemCombobox.svelte';
  import { buildHomeSearchUrl } from '$lib/features/home/search-url';

  let selected = $state<AutocompleteSystem | null>(null);
  let archetype = $state<string>(ANY_ARCHETYPE);
  let attempted = $state(false);
  let referenceInput: ReferenceSystemCombobox;
  const invalid = $derived(attempted && selected === null);

  function submit(event: SubmitEvent) {
    event.preventDefault();
    const destination = buildHomeSearchUrl(
      selected,
      archetype,
      FINDER_F4_ENABLED,
    );
    if (!destination) {
      attempted = true;
      referenceInput.focus();
      return;
    }
    void goto(resolve(destination));
  }

  const steps = [
    {
      number: '01',
      href: '/explore',
      label: 'Find your system',
      detail: 'Choose an archetype and refine your search.',
    },
    {
      number: '02',
      href: '/inspect',
      label: 'Understand its potential',
      detail: 'Inspect bodies, resources and rating evidence.',
    },
    {
      number: '03',
      href: '/plan',
      label: 'Plan your colony',
      detail: 'Choose sites, order builds and review your plan.',
    },
  ] as const;
</script>

<svelte:head>
  <title>ED-Finder — Find your next frontier</title>
  <meta
    name="description"
    content="Discover promising Elite Dangerous systems. Compare colony potential and plan what comes next."
  />
</svelte:head>

<WorkspaceHeader />
<main class="home-page">
  <section class="home-hero" aria-labelledby="home-heading">
    <picture class="hero-art" aria-hidden="true">
      <source
        media="(max-width: 640px)"
        srcset={asset('/assets/home-hero-black-hole-960.webp')}
      />
      <img
        src={asset('/assets/home-hero-black-hole-1920.webp')}
        alt=""
        aria-hidden="true"
        width="1920"
        height="1080"
        fetchpriority="high"
      />
    </picture>
    <div class="hero-copy">
      <p class="eyebrow">Your colony starts here</p>
      <h1 id="home-heading">Find your next <br />frontier<span>.</span></h1>
      <p class="lede">
        Discover promising systems.<br />
        Compare colony potential. Plan what comes next.
      </p>
      <form
        class="home-find-form"
        class:with-archetype={FINDER_F4_ENABLED}
        aria-label="Find a system"
        method="get"
        action={resolve('/explore')}
        onsubmit={submit}
      >
        <ReferenceSystemCombobox
          bind:this={referenceInput}
          bind:selected
          {invalid}
          describedBy={invalid ? 'home-reference-error' : undefined}
        />
        {#if FINDER_F4_ENABLED}
          <div class="archetype-field">
            <label for="home-archetype">Colony archetype</label>
            <select id="home-archetype" bind:value={archetype}>
              <option value={ANY_ARCHETYPE}>Any archetype</option>
              {#each ARCHETYPES as entry (entry.key)}
                <option value={entry.key}>{entry.label}</option>
              {/each}
            </select>
          </div>
        {/if}
        <button type="submit" class="primary-button">Find a system →</button>
        {#if invalid}
          <p id="home-reference-error" class="form-error" role="alert">
            Choose a system from the list
          </p>
        {/if}
      </form>
      <a class="open-explore" href={resolve('/explore')}
        >Or open Explore without a reference system</a
      >
      <p class="hero-note">
        Explore freely. Sign in with Frontier when you need your commander
        account.
      </p>
    </div>
  </section>

  <section class="home-steps" aria-label="Your colony journey">
    {#each steps as step (step.href)}
      <a class="journey-step" href={resolve(step.href)}>
        <span class="step-number">{step.number}</span>
        <h2>{step.label}</h2>
        <p>{step.detail}</p>
        <ArrowUpRight class="step-arrow" size={20} aria-hidden="true" />
      </a>
    {/each}
  </section>
  <div class="home-status"><BootstrapStatus /></div>
</main>
<footer class="site-footer">
  <div class="footer-line">
    <span><strong>ED:FINDER</strong> · Your Elite Dangerous companion.</span>
    <span>Explore. Discover. Make it yours.</span>
  </div>
  <p class="frontier-credit">
    ED-Finder was created using assets and imagery from Elite Dangerous, with
    the permission of Frontier Developments plc, for non-commercial purposes. It
    is not endorsed by nor reflects the views or opinions of Frontier
    Developments and no employee of Frontier Developments was involved in the
    making of it.
  </p>
</footer>

<style>
  .home-page {
    width: 100%;
  }

  /* The home composition remains still, including the shared kit hover paint. */
  main.home-page :global(:is(a, button, input, select):hover),
  main.home-page :global(:is(a, button, input, select):focus-visible) {
    transition: none;
  }

  .home-hero {
    position: relative;
    isolation: isolate;
    display: flex;
    align-items: center;
    min-height: calc(100svh - 6rem);
    padding: 4rem max(3rem, calc((100% - 80rem) / 2));
    background: var(--bg-void);
  }

  .hero-art {
    position: absolute;
    z-index: -2;
    inset: 0 0 0 18%;
    overflow: hidden;
    pointer-events: none;
  }

  .hero-art img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center;
  }

  /* Token-only scrim: all desktop copy ends before the 52% stop. Even over
     white pixels, 94% --panel yields >=6.55:1 for --text-muted (see CHANGES).
     The photo stays decorative and its loading cannot change hero geometry. */
  .home-hero::before {
    content: '';
    position: absolute;
    z-index: -1;
    inset: 0;
    pointer-events: none;
    background: linear-gradient(
      90deg,
      var(--bg-void) 0%,
      color-mix(in srgb, var(--panel) 94%, transparent) 52%,
      color-mix(in srgb, var(--bg-void) 45%, transparent) 70%,
      color-mix(in srgb, var(--bg-void) 8%, transparent) 100%
    );
  }

  .hero-copy {
    width: 46%;
    max-width: 38rem;
    min-width: 0;
  }

  .eyebrow,
  .step-number {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 0;
    color: var(--accent);
    font-family: var(--font-hud);
    font-size: 0.8125rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    line-height: 1.5;
    text-transform: uppercase;
  }

  .eyebrow::after,
  .step-number::after {
    content: '';
    width: 3rem;
    height: 1px;
    background: var(--accent);
  }

  .home-hero h1 {
    margin: 1.25rem 0 1.5rem;
    font-family: var(--font-hud);
    font-size: clamp(3.25rem, 6vw, 5.5rem);
    font-weight: 700;
    line-height: 1.03;
    letter-spacing: 0.01em;
    text-transform: none;
  }

  .home-hero h1 span {
    color: var(--accent);
  }

  .lede {
    margin: 0;
    color: var(--text-muted);
    font-size: 1rem;
    line-height: 1.75;
  }

  .home-find-form {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 0.75rem;
    margin-top: 2rem;
  }

  .home-find-form.with-archetype {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  }

  .archetype-field {
    display: grid;
    gap: 0.5rem;
    min-width: 0;
  }

  .archetype-field label {
    color: var(--text-muted);
    font-size: 0.75rem;
    line-height: 1.5;
  }

  .archetype-field select {
    width: 100%;
    min-width: 0;
    height: 3rem;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 0.6rem;
    background: var(--panel-sunken);
    color: var(--text);
    font: inherit;
    font-size: 0.875rem;
  }

  .home-find-form button {
    min-height: 3rem;
    padding-inline: 1rem;
    white-space: nowrap;
    font-size: 0.875rem;
  }

  .form-error {
    grid-column: 1 / -1;
    margin: 0;
    color: var(--text);
    font-size: 0.875rem;
    line-height: 1.5;
  }

  .open-explore {
    display: inline-block;
    margin-top: 1rem;
    color: var(--text);
    font-size: 0.8125rem;
    line-height: 1.5;
    text-underline-offset: 0.2em;
  }

  .hero-note {
    margin: 1rem 0 0;
    color: var(--text-muted);
    font-size: 0.8125rem;
    line-height: 1.6;
  }

  .home-steps,
  .home-status,
  .site-footer {
    width: min(80rem, calc(100% - 6rem));
    margin-inline: auto;
  }

  .home-steps {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 3rem;
    padding-block: 3rem;
  }

  .journey-step {
    position: relative;
    display: block;
    border-radius: var(--radius-sm);
    padding-right: 1.75rem;
    text-decoration: none;
  }

  .step-number {
    font-size: 1.25rem;
  }

  .journey-step h2 {
    margin: 1rem 0 0.5rem;
    font-family: var(--font-hud);
    font-size: 1.5rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    line-height: 1.2;
  }

  .journey-step p {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.875rem;
    line-height: 1.6;
  }

  .journey-step :global(.step-arrow) {
    position: absolute;
    top: 0.3rem;
    right: 0;
    color: var(--accent);
  }

  .journey-step:hover h2 {
    text-decoration: underline;
    text-underline-offset: 0.2em;
  }

  .home-status :global(.status-panel) {
    margin: 0 0 2rem;
    padding: 1rem 1.5rem;
    gap: 1rem;
  }

  .home-status :global(.connection-label) {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.875rem;
  }

  .home-status :global(.status-panel dl) {
    margin: 0;
    gap: 2rem;
  }

  .home-status :global(.status-panel dt) {
    color: var(--text-muted);
    font-size: 0.75rem;
    letter-spacing: 0.06em;
  }

  .home-status :global(.status-panel dd) {
    font-size: 0.875rem;
  }

  .site-footer {
    padding-block: 1.5rem 2rem;
    border-top: 1px solid var(--line);
    color: var(--text-muted);
    font-size: 0.75rem;
    line-height: 1.7;
  }

  .footer-line {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .site-footer strong {
    color: var(--text);
    letter-spacing: 0.06em;
  }

  .frontier-credit {
    max-width: 150ch;
    margin: 1.25rem 0 0;
  }

  @media (max-width: 1100px) {
    .home-find-form.with-archetype {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .with-archetype button {
      grid-column: 1 / -1;
    }
  }

  @media (max-width: 900px) {
    .home-hero {
      padding-inline: 2rem;
    }

    .hero-copy {
      width: 70%;
    }

    /* Wider copy on tablets retains the same worst-case 94% token scrim. */
    .home-hero::before {
      background: linear-gradient(
        90deg,
        var(--bg-void),
        color-mix(in srgb, var(--panel) 94%, transparent) 78%,
        color-mix(in srgb, var(--bg-void) 45%, transparent)
      );
    }

    .home-steps,
    .home-status,
    .site-footer {
      width: calc(100% - 4rem);
    }

    .home-steps {
      gap: 1.5rem;
    }
  }

  @media (max-width: 640px) {
    .home-hero {
      min-height: calc(100svh - 10rem);
      padding: 3rem 1.5rem;
    }

    .hero-art {
      inset-inline-start: 0;
    }

    .hero-art img {
      object-position: 60% center;
    }

    .home-hero::before {
      background: linear-gradient(
        90deg,
        color-mix(in srgb, var(--bg-void) 97%, transparent),
        color-mix(in srgb, var(--panel) 94%, transparent)
      );
    }

    .hero-copy {
      width: 100%;
    }

    .home-hero h1 {
      font-size: clamp(2.75rem, 12vw, 4rem);
    }

    .home-find-form,
    .home-find-form.with-archetype,
    .home-steps {
      grid-template-columns: minmax(0, 1fr);
    }

    .home-steps,
    .home-status,
    .site-footer {
      width: calc(100% - 3rem);
    }

    .home-steps {
      gap: 2rem;
      padding-block: 2.5rem;
    }

    .home-status :global(.status-panel) {
      align-items: flex-start;
      flex-direction: column;
      padding: 1rem;
    }

    .home-status :global(.status-panel dl) {
      flex-wrap: wrap;
      gap: 1rem 2rem;
    }
  }
</style>
