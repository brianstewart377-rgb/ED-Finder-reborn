<script lang="ts">
  import type {
    EconomyRatingExplanation,
    RatingsIdentity,
  } from '$lib/api/client';
  import {
    adjustmentSummary,
    contributionRole,
    evidenceLabel,
    percent,
    specialisationLabel,
  } from './system-detail-format';

  type Props = {
    explained: EconomyRatingExplanation;
    identity: RatingsIdentity;
    bodyNames: ReadonlyMap<string, string>;
  };
  let { explained, identity, bodyNames }: Props = $props();

  const bodyName = (bodyKey: string): string =>
    bodyNames.get(bodyKey) ?? `Body ${bodyKey}`;

  const unknownEvidence = $derived(
    explained.contributions
      .filter((contribution) => contribution.unknownEvidence.length > 0)
      .map((contribution) => ({
        bodyKey: contribution.bodyKey,
        features: contribution.unknownEvidence.map(evidenceLabel).join(', '),
      })),
  );
</script>

<dl class="rating-facts">
  <div>
    <dt>Evidence known</dt>
    <dd>{percent(explained.completeness)}</dd>
  </div>
  <div>
    <dt>Specialisation</dt>
    <dd>{specialisationLabel(explained)}</dd>
  </div>
</dl>
{#if explained.contributions.length === 0}
  <p>No catalogued body offers this economy.</p>
{:else}
  <table class="rating-contributions">
    <caption>Bodies that make up this score</caption>
    <thead>
      <tr>
        <th scope="col">Body</th>
        <th scope="col">Role</th>
        <th scope="col">Score</th>
        <th scope="col">Weight</th>
      </tr>
    </thead>
    <tbody>
      {#each explained.contributions as contribution (contribution.bodyKey)}
        {@const adjustments = adjustmentSummary(contribution)}
        <tr>
          <th scope="row">{bodyName(contribution.bodyKey)}</th>
          <td>
            <div>{contributionRole(contribution)}</div>
            {#if adjustments}
              <div class="rating-adjustments">
                Base {contribution.base}; {adjustments}
              </div>
            {/if}
          </td>
          <td>{contribution.local}</td>
          <td>{percent(contribution.weight)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}
{#if unknownEvidence.length > 0}
  <div class="rating-unknown">
    <p>Not yet known, so this rating may change:</p>
    <ul>
      {#each unknownEvidence as item (item.bodyKey)}
        <li>{bodyName(item.bodyKey)}: {item.features}</li>
      {/each}
    </ul>
  </div>
{/if}
<p class="rating-version">
  Scorer {identity.scorerVersion} · mechanics {identity.mechanicsVersion}
</p>
