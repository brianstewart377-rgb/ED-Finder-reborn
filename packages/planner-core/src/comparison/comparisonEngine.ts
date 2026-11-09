import type {
  BuildComparisonResult,
  BuildComparisonSource,
  ComparisonRankingDelta,
  ComparisonRiskDelta,
  FacilityCountDelta,
  NumericDelta,
  PlacementChange,
  StringSetChanges,
} from './types';

const EPSILON = 0.000001;

export function compareBuildSources(
  before: BuildComparisonSource,
  after: BuildComparisonSource,
): BuildComparisonResult {
  const placementComparison = comparePlacements(before.placements, after.placements);
  const previewSummaryDelta = {
    final_score: numericDelta(before.previewSummary?.final_score, after.previewSummary?.final_score),
    composition_score: numericDelta(before.previewSummary?.composition_score, after.previewSummary?.composition_score),
    buildability_score: numericDelta(before.previewSummary?.buildability_score, after.previewSummary?.buildability_score),
    confidence: numericDelta(before.previewSummary?.confidence, after.previewSummary?.confidence),
  };
  const rankingDelta = compareRanking(before, after);
  const warningChanges = stringSetChanges(before.warnings ?? [], after.warnings ?? []);
  const assumptionChanges = stringSetChanges(before.assumptions ?? [], after.assumptions ?? []);
  const riskDelta = compareRisk(before, after);

  const base: Omit<BuildComparisonResult, 'tradeoff_summary' | 'recommendation'> = {
    before: sourceSummary(before),
    after: sourceSummary(after),
    target_archetype_changed: (before.targetArchetype ?? null) !== (after.targetArchetype ?? null),
    before_target_archetype: before.targetArchetype ?? null,
    after_target_archetype: after.targetArchetype ?? null,
    facility_count_deltas: facilityCountDeltas(before.placements, after.placements),
    added_facilities: placementComparison.added,
    removed_facilities: placementComparison.removed,
    changed_placements: placementComparison.changed,
    unchanged_placements_count: placementComparison.unchangedCount,
    primary_port_change: primaryPortChange(before.placements, after.placements),
    preview_summary_delta: previewSummaryDelta,
    ranking_delta: rankingDelta,
    risk_delta: riskDelta,
    warning_changes: warningChanges,
    assumption_changes: assumptionChanges,
  };

  const tradeoffSummary = buildTradeoffSummary(base);
  return {
    ...base,
    tradeoff_summary: tradeoffSummary,
    recommendation: buildRecommendation(base),
  };
}

function sourceSummary(source: BuildComparisonSource) {
  return {
    id: source.id,
    label: source.label,
    kind: source.kind,
    targetArchetype: source.targetArchetype ?? null,
  };
}

export function numericDelta(
  before?: number | null,
  after?: number | null,
  higherIsBetter = true,
): NumericDelta {
  if (before == null || after == null || Number.isNaN(before) || Number.isNaN(after)) {
    return { before: before ?? null, after: after ?? null, delta: null, direction: 'unknown' };
  }
  const delta = after - before;
  if (Math.abs(delta) < EPSILON) {
    return { before, after, delta: 0, direction: 'unchanged' };
  }
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  return { before, after, delta, direction: improved ? 'improved' : 'worsened' };
}

function compareRanking(before: BuildComparisonSource, after: BuildComparisonSource): ComparisonRankingDelta | null {
  const beforeRank = before.ranking;
  const afterRank = after.ranking;
  if (!beforeRank && !afterRank) return null;
  const scoreDelta = numericDelta(beforeRank?.rank_score, afterRank?.rank_score);
  const rankDelta = numericDelta(beforeRank?.rank, afterRank?.rank, false);
  const direction = scoreDelta.direction !== 'unknown' ? scoreDelta.direction : rankDelta.direction;
  return {
    before_rank: beforeRank?.rank ?? null,
    after_rank: afterRank?.rank ?? null,
    before_rank_score: beforeRank?.rank_score ?? null,
    after_rank_score: afterRank?.rank_score ?? null,
    rank_score_delta: scoreDelta.delta ?? null,
    before_rank_tier: beforeRank?.rank_tier ?? null,
    after_rank_tier: afterRank?.rank_tier ?? null,
    direction,
  };
}

function compareRisk(before: BuildComparisonSource, after: BuildComparisonSource): ComparisonRiskDelta {
  const beforeWarnings = totalWarningCount(before);
  const afterWarnings = totalWarningCount(after);
  const warningCount = numericDelta(beforeWarnings, afterWarnings, false);
  const beforeCp = before.previewSummary?.cp_negative ?? null;
  const afterCp = after.previewSummary?.cp_negative ?? null;
  const cpKnown = beforeCp != null && afterCp != null;
  const cpWorsened = cpKnown && beforeCp === false && afterCp === true;
  const cpImproved = cpKnown && beforeCp === true && afterCp === false;

  let riskDirection: ComparisonRiskDelta['risk_direction'] = 'unknown';
  if (warningCount.direction !== 'unknown' || cpKnown) {
    if (cpWorsened || warningCount.direction === 'worsened') riskDirection = 'higher';
    else if (cpImproved || warningCount.direction === 'improved') riskDirection = 'lower';
    else riskDirection = 'unchanged';
  }

  return {
    warning_count: warningCount,
    cp_negative_changed: cpKnown ? beforeCp !== afterCp : false,
    before_cp_negative: beforeCp,
    after_cp_negative: afterCp,
    risk_direction: riskDirection,
  };
}

function totalWarningCount(source: BuildComparisonSource): number {
  return (source.warnings ?? []).length + (source.previewSummary?.warnings_count ?? 0);
}

export function stringSetChanges(before: string[], after: string[]): StringSetChanges {
  const beforeSet = normalizeStringSet(before);
  const afterSet = normalizeStringSet(after);
  return {
    before: [...beforeSet],
    after: [...afterSet],
    added: [...afterSet].filter((item) => !beforeSet.has(item)),
    removed: [...beforeSet].filter((item) => !afterSet.has(item)),
    shared: [...beforeSet].filter((item) => afterSet.has(item)),
  };
}

function normalizeStringSet(items: string[]): Set<string> {
  return new Set(items.map((item) => item.trim()).filter(Boolean).sort((a, b) => a.localeCompare(b)));
}

function facilityCountDeltas(before: BuildComparisonSource['placements'], after: BuildComparisonSource['placements']): FacilityCountDelta[] {
  const beforeCounts = countByFacility(before);
  const afterCounts = countByFacility(after);
  const ids = [...new Set([...beforeCounts.keys(), ...afterCounts.keys()])].sort((a, b) => a.localeCompare(b));
  return ids
    .map((id) => {
      const beforeCount = beforeCounts.get(id) ?? 0;
      const afterCount = afterCounts.get(id) ?? 0;
      return { facility_template_id: id, before_count: beforeCount, after_count: afterCount, delta: afterCount - beforeCount };
    })
    .filter((delta) => delta.delta !== 0);
}

function countByFacility(placements: BuildComparisonSource['placements']): Map<string, number> {
  const counts = new Map<string, number>();
  for (const placement of placements) {
    counts.set(placement.facility_template_id, (counts.get(placement.facility_template_id) ?? 0) + 1);
  }
  return counts;
}

function comparePlacements(before: BuildComparisonSource['placements'], after: BuildComparisonSource['placements']) {
  const beforeRemaining = before.map((placement, index) => ({ placement, index, matched: false }));
  const afterRemaining = after.map((placement, index) => ({ placement, index, matched: false }));
  const added: PlacementChange[] = [];
  const removed: PlacementChange[] = [];
  const changed: PlacementChange[] = [];
  let unchangedCount = 0;

  for (const beforeItem of beforeRemaining) {
    const exact = afterRemaining.find((afterItem) => !afterItem.matched && sameIdentity(beforeItem.placement, afterItem.placement));
    if (!exact) continue;
    beforeItem.matched = true;
    exact.matched = true;
    const changeType = placementChangeType(beforeItem.placement, exact.placement);
    if (changeType === 'unchanged') unchangedCount += 1;
    else changed.push(changeFromPair(beforeItem.placement, exact.placement, changeType));
  }

  for (const beforeItem of beforeRemaining.filter((item) => !item.matched)) {
    const moved = afterRemaining.find((afterItem) => !afterItem.matched && beforeItem.placement.facility_template_id === afterItem.placement.facility_template_id);
    if (!moved) continue;
    beforeItem.matched = true;
    moved.matched = true;
    changed.push(changeFromPair(beforeItem.placement, moved.placement, 'body_changed'));
  }

  for (const beforeItem of beforeRemaining.filter((item) => !item.matched)) {
    removed.push(changeFromBefore(beforeItem.placement));
  }
  for (const afterItem of afterRemaining.filter((item) => !item.matched)) {
    added.push(changeFromAfter(afterItem.placement));
  }

  return {
    added: sortChanges(added),
    removed: sortChanges(removed),
    changed: sortChanges(changed),
    unchangedCount,
  };
}

function sameIdentity(a: BuildComparisonSource['placements'][number], b: BuildComparisonSource['placements'][number]): boolean {
  return a.facility_template_id === b.facility_template_id && (a.local_body_id ?? null) === (b.local_body_id ?? null);
}

function placementChangeType(
  before: BuildComparisonSource['placements'][number],
  after: BuildComparisonSource['placements'][number],
): PlacementChange['change_type'] {
  if (Boolean(before.is_primary_port) !== Boolean(after.is_primary_port)) return 'primary_port_changed';
  if (before.build_order !== after.build_order) return 'order_changed';
  return 'unchanged';
}

function changeFromPair(
  before: BuildComparisonSource['placements'][number],
  after: BuildComparisonSource['placements'][number],
  changeType: PlacementChange['change_type'],
): PlacementChange {
  return {
    facility_template_id: after.facility_template_id,
    before_body_id: before.local_body_id ?? null,
    after_body_id: after.local_body_id ?? null,
    before_build_order: before.build_order,
    after_build_order: after.build_order,
    before_primary_port: Boolean(before.is_primary_port),
    after_primary_port: Boolean(after.is_primary_port),
    change_type: changeType,
  };
}

function changeFromBefore(before: BuildComparisonSource['placements'][number]): PlacementChange {
  return {
    facility_template_id: before.facility_template_id,
    before_body_id: before.local_body_id ?? null,
    after_body_id: null,
    before_build_order: before.build_order,
    after_build_order: null,
    before_primary_port: Boolean(before.is_primary_port),
    after_primary_port: null,
    change_type: 'removed',
  };
}

function changeFromAfter(after: BuildComparisonSource['placements'][number]): PlacementChange {
  return {
    facility_template_id: after.facility_template_id,
    before_body_id: null,
    after_body_id: after.local_body_id ?? null,
    before_build_order: null,
    after_build_order: after.build_order,
    before_primary_port: null,
    after_primary_port: Boolean(after.is_primary_port),
    change_type: 'added',
  };
}

function sortChanges(changes: PlacementChange[]): PlacementChange[] {
  return [...changes].sort((a, b) => {
    const facility = a.facility_template_id.localeCompare(b.facility_template_id);
    if (facility !== 0) return facility;
    return (a.after_build_order ?? a.before_build_order ?? 0) - (b.after_build_order ?? b.before_build_order ?? 0);
  });
}

function primaryPortChange(before: BuildComparisonSource['placements'], after: BuildComparisonSource['placements']): PlacementChange | null {
  const beforePort = before.find((placement) => placement.is_primary_port);
  const afterPort = after.find((placement) => placement.is_primary_port);
  if (!beforePort && !afterPort) return null;
  if (beforePort && afterPort && sameIdentity(beforePort, afterPort)) {
    const changeType = placementChangeType(beforePort, afterPort);
    return changeType === 'unchanged' ? null : changeFromPair(beforePort, afterPort, changeType);
  }
  return {
    facility_template_id: afterPort?.facility_template_id ?? beforePort?.facility_template_id ?? 'unknown',
    before_body_id: beforePort?.local_body_id ?? null,
    after_body_id: afterPort?.local_body_id ?? null,
    before_build_order: beforePort?.build_order ?? null,
    after_build_order: afterPort?.build_order ?? null,
    before_primary_port: beforePort?.is_primary_port ?? null,
    after_primary_port: afterPort?.is_primary_port ?? null,
    change_type: 'primary_port_changed',
  };
}

function buildTradeoffSummary(result: Omit<BuildComparisonResult, 'tradeoff_summary' | 'recommendation'>): string[] {
  const summary: string[] = [];
  if (result.target_archetype_changed) {
    summary.push(`Changes target archetype from ${result.before_target_archetype ?? 'unknown'} to ${result.after_target_archetype ?? 'unknown'}.`);
  }
  if (result.added_facilities.length > 0 || result.removed_facilities.length > 0) {
    summary.push(`Adds ${result.added_facilities.length} ${plural('facility', result.added_facilities.length)} and removes ${result.removed_facilities.length} ${plural('facility', result.removed_facilities.length)}.`);
  }
  if (result.primary_port_change) {
    summary.push(`Moves primary port from ${result.primary_port_change.before_body_id ?? 'none'} to ${result.primary_port_change.after_body_id ?? 'none'}.`);
  }
  const finalScore = result.preview_summary_delta.final_score;
  if (finalScore.direction === 'improved') summary.push(`Improves final score by ${formatNumber(finalScore.delta)} points.`);
  if (finalScore.direction === 'worsened') summary.push(`Reduces final score by ${formatNumber(Math.abs(finalScore.delta ?? 0))} points.`);
  const warningDelta = result.risk_delta.warning_count.delta ?? 0;
  if (result.risk_delta.warning_count.direction === 'improved') summary.push(`Reduces warning count by ${Math.abs(warningDelta)}.`);
  if (result.risk_delta.warning_count.direction === 'worsened') summary.push(`Increases warning count by ${Math.abs(warningDelta)}.`);
  if (result.risk_delta.before_cp_negative === false && result.risk_delta.after_cp_negative === true) summary.push('Introduces CP risk.');
  if (result.risk_delta.before_cp_negative === true && result.risk_delta.after_cp_negative === false) summary.push('Removes CP risk.');
  if (summary.length === 0) summary.push('No major placement changes detected.');
  return summary.slice(0, 6);
}

function buildRecommendation(result: Omit<BuildComparisonResult, 'tradeoff_summary' | 'recommendation'>): BuildComparisonResult['recommendation'] {
  const scoreDirection = result.preview_summary_delta.final_score.direction;
  const rankDirection = result.ranking_delta?.direction ?? 'unknown';
  const riskDirection = result.risk_delta.risk_direction;
  const reasons: string[] = [];

  if (scoreDirection === 'unknown' && rankDirection === 'unknown' && (riskDirection === 'unknown' || riskDirection === 'unchanged')) {
    return { verdict: 'insufficient_data', reasons: ['No score, ranking, or risk signal is available.'] };
  }
  if ((scoreDirection === 'improved' || rankDirection === 'improved') && riskDirection !== 'higher') {
    reasons.push(scoreDirection === 'improved' ? 'Final score improves without higher risk.' : 'Ranking signal improves without higher risk.');
    return { verdict: 'prefer_after', reasons };
  }
  if (scoreDirection === 'worsened' || (riskDirection === 'higher' && scoreDirection !== 'improved')) {
    reasons.push(scoreDirection === 'worsened' ? 'Final score worsens.' : 'Risk increases without a final-score improvement.');
    return { verdict: 'prefer_before', reasons };
  }
  if ((scoreDirection === 'improved' && riskDirection === 'higher') || result.preview_summary_delta.confidence.direction === 'worsened') {
    reasons.push(scoreDirection === 'improved' && riskDirection === 'higher' ? 'Score improves, but risk also increases.' : 'Confidence worsens, so the tradeoff is mixed.');
    return { verdict: 'mixed', reasons };
  }
  return { verdict: 'mixed', reasons: ['Available signals do not clearly prefer either plan.'] };
}

function plural(label: string, count: number): string {
  return count === 1 ? label : `${label}s`;
}

function formatNumber(value?: number | null): string {
  if (value == null) return '0';
  return value.toFixed(Math.abs(value) % 1 === 0 ? 0 : 1);
}
