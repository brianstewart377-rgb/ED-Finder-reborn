<script lang="ts">
  import type { EconomyV1 } from '$lib/api';
  import {
    CONTRIBUTION_LABELS,
    economyChange,
    economyColour,
    economyPercent,
    economyRuleIds,
    economyTotal,
    type EconomyWhySelection,
  } from './economy-presentation';

  type Props = {
    siteId: string;
    view: 'now' | 'after_plan';
    now: EconomyV1[];
    after: EconomyV1[];
    scale: number;
    siteNames: Record<string, string>;
    onwhy: (selection: EconomyWhySelection) => void;
  };
  let { siteId, view, now, after, scale, siteNames, onwhy }: Props = $props();
  const id = $props.id();
  let focused = $state<string | null>(null);
  let hovered = $state<string | null>(null);
  let dismissed = $state(false);
  const label = $derived(view === 'now' ? 'Now' : 'After plan');
  const economies = $derived(view === 'now' ? now : after);
  const total = $derived(economyTotal(economies));
  const active = $derived(
    dismissed
      ? undefined
      : economies.find((economy) => economy.name === (focused ?? hovered)),
  );

  function why(name: string) {
    onwhy({
      siteId,
      economyName: name,
      ruleIds: economyRuleIds(name, now, after),
    });
  }
</script>

<div
  class="economy-row"
  data-economy-view={view}
  role="group"
  aria-labelledby={`${id}-label`}
  onmouseleave={() => {
    hovered = null;
  }}
>
  <h4 id={`${id}-label`}>{label}</h4>
  {#if economies.length === 0}
    <p class="muted">No economy</p>
  {:else}
    <div class="economy-track">
      {#each economies as economy, index (economy.name)}
        {@const change = economyChange(economy.name, now, after)}
        <button
          type="button"
          class="economy-segment"
          class:changed={change !== null}
          data-economy-name={economy.name}
          data-economy-change={change ?? undefined}
          style:width={`${(economy.strength / scale) * 100}%`}
          style:--economy-colour={economyColour(economy.name)}
          aria-label={`${label}: ${economy.name} ${economyPercent(economy.strength)}. ${change ? `${change}. ` : ''}Open economy rules.`}
          aria-describedby={active?.name === economy.name
            ? `${id}-breakdown-${index}`
            : undefined}
          onfocus={() => {
            focused = economy.name;
            dismissed = false;
          }}
          onblur={() => {
            focused = null;
          }}
          onmouseenter={() => {
            hovered = economy.name;
            dismissed = false;
          }}
          onkeydown={(event) => {
            if (event.key === 'Escape') {
              dismissed = true;
              event.stopPropagation();
            }
          }}
          onclick={() => why(economy.name)}
          ><span class="segment-label" aria-hidden="true"
            >{economyPercent(economy.strength)}</span
          ></button
        >
      {/each}
    </div>
    <ul class="economy-legend" aria-label={`${label} economy legend`}>
      {#each economies as economy (economy.name)}
        {@const change = economyChange(economy.name, now, after)}
        <li>
          <span
            class="swatch"
            style:background={economyColour(economy.name)}
            aria-hidden="true"
          ></span>
          <span
            >{economy.name}
            <strong>{economyPercent(economy.strength)}</strong></span
          >
          {#if change}<span class="change">{change}</span>{/if}
        </li>
      {/each}
    </ul>
    {#if Math.abs(total - 1) > 1e-9}
      <p class="muted" data-economy-total-note>
        Returned shares total {economyPercent(total)}, not 100%. Values are
        shown as returned, without normalizing.
      </p>
    {/if}
    {#if active}
      <div
        id={`${id}-breakdown-${economies.indexOf(active)}`}
        class="economy-breakdown"
        role="tooltip"
      >
        <strong
          >{active.name}: {economyPercent(active.strength)} — {label}</strong
        >
        <dl>
          {#each Object.entries(CONTRIBUTION_LABELS) as [kind, title] (kind)}
            <div>
              <dt>{title}</dt>
              <dd>
                {#each active.items.filter((item) => item.kind === kind) as item, itemIndex (itemIndex)}
                  <p>
                    {economyPercent(item.value)} (base {economyPercent(
                      item.base,
                    )})
                    {#if item.modifiers.length}
                      · Modifiers: {item.modifiers
                        .map(
                          (modifier) =>
                            `${modifier.condition} ${modifier.delta >= 0 ? '+' : ''}${economyPercent(modifier.delta)}`,
                        )
                        .join('; ')}
                    {/if}
                    {#if item.floored}
                      · Minimum applied{/if}
                    {#if item.count !== null}
                      · Count: {item.count}{/if}
                    {#if item.source_site_ids.length}
                      · From: {item.source_site_ids
                        .map((source) => siteNames[source] || source)
                        .join(', ')}
                    {/if}
                    {#if item.via_site_id !== null}
                      · Via: {siteNames[item.via_site_id] || item.via_site_id}
                    {/if}
                    {#if item.rule_ids.length}
                      · Rules: {item.rule_ids.join(', ')}{/if}
                  </p>
                {:else}
                  None returned
                {/each}
              </dd>
            </div>
          {/each}
        </dl>
        <p class="muted">
          Activate the economy to open its rules in Why. Escape hides this
          breakdown.
        </p>
      </div>
    {/if}
  {/if}
</div>

<style>
  .economy-row {
    min-width: 0;
  }
  h4 {
    margin: 0 0 0.5rem;
    font-size: 0.85rem;
  }
  p {
    margin: 0.35rem 0;
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .muted {
    color: var(--text-muted, var(--muted));
  }
  .economy-track {
    display: flex;
    width: 100%;
    height: 2rem;
    background: var(--panel-raised);
    border-radius: 0.35rem;
  }
  .economy-track .economy-segment {
    position: relative;
    flex: 0 0 auto;
    min-width: 0;
    max-width: none;
    height: 100%;
    padding: 0;
    border: 1px solid var(--color-panel);
    border-radius: 0;
    background: var(--economy-colour);
    color: var(--accent-text);
    cursor: pointer;
  }
  .economy-segment:first-child {
    border-radius: 0.35rem 0 0 0.35rem;
  }
  .economy-segment:last-child {
    border-radius: 0 0.35rem 0.35rem 0;
  }
  .economy-track .economy-segment:focus-visible {
    z-index: 1;
    outline: 2px solid var(--text);
    outline-offset: 2px;
  }
  .segment-label {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    font-size: 0.75rem;
    font-weight: 700;
  }
  .economy-track .changed {
    box-shadow: inset 0 -4px var(--accent-text);
  }
  .economy-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    padding: 0;
    margin: 0.65rem 0;
    list-style: none;
    font-size: 0.8rem;
  }
  .economy-legend li {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .swatch {
    width: 0.7rem;
    height: 0.7rem;
    flex-shrink: 0;
    border-radius: 0.15rem;
  }
  .change {
    color: var(--text-muted, var(--muted));
    text-decoration: underline dotted;
    text-underline-offset: 0.2em;
  }
  .economy-breakdown {
    margin-top: 0.75rem;
    padding: 0.75rem;
    border: 1px solid var(--line);
    border-radius: 0.35rem;
    background: var(--panel-raised);
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }
  dl {
    margin: 0.6rem 0;
  }
  dl > div {
    display: grid;
    grid-template-columns: minmax(5rem, 1fr) minmax(0, 4fr);
    gap: 0.5rem;
    margin-top: 0.4rem;
  }
  dt {
    font-weight: 650;
  }
  dd {
    margin: 0;
  }
  dd p:first-child {
    margin-top: 0;
  }
</style>
