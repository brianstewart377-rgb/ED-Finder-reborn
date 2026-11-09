import { describe, expect, it } from 'vitest';
import type { OptimiserCandidate, RankedOptimiserCandidate, SimulateBuildPlacement } from '@ed-finder/api-client/types';
import {
  compareBuildSources,
  formatDeltaValue,
  formatRiskDirection,
  formatVerdictLabel,
  numericDelta,
  sourceFromCurrentPreview,
  sourceFromOptimiserCandidate,
  stringSetChanges,
} from '.';
import type { BuildComparisonSource } from './types';

function placement(
  facility_template_id: string,
  local_body_id: string | null,
  build_order: number,
  is_primary_port = false,
): SimulateBuildPlacement {
  return { facility_template_id, local_body_id, build_order, is_primary_port };
}

function source(overrides: Partial<BuildComparisonSource> = {}): BuildComparisonSource {
  return {
    id: 'before',
    label: 'Before plan',
    kind: 'current_preview',
    targetArchetype: 'refinery_industrial',
    placements: [
      placement('port_a', 'body1', 1, true),
      placement('support_x', 'body1', 2),
    ],
    previewSummary: {
      final_score: 70,
      composition_score: 60,
      buildability_score: 65,
      confidence: 0.7,
      build_complexity: 'moderate',
      warnings_count: 1,
      cp_negative: false,
      top_two_alignment: 'weak',
    },
    ranking: {
      candidate_id: 'before',
      rank: 2,
      rank_score: 70,
      rank_tier: 'strong',
      rank_breakdown: {
        preview_score_component: 20,
        composition_component: 10,
        buildability_component: 10,
        confidence_component: 10,
        alignment_component: 2,
        warning_penalty: -1,
        cp_penalty: 0,
        strategy_modifier: 0,
        total_score: 70,
        reasons: [],
      },
    },
    warnings: ['Existing warning'],
    assumptions: ['Existing assumption'],
    tags: [],
    strategy: null,
    ...overrides,
  };
}

function after(overrides: Partial<BuildComparisonSource> = {}): BuildComparisonSource {
  return source({
    id: 'after',
    label: 'After candidate',
    kind: 'optimiser_candidate',
    targetArchetype: 'agriculture_terraforming',
    placements: [
      placement('port_a', 'body2', 1, true),
      placement('support_x', 'body1', 3),
      placement('support_y', 'body2', 2),
    ],
    previewSummary: {
      final_score: 78.4,
      composition_score: 67,
      buildability_score: 66,
      confidence: 0.8,
      build_complexity: 'moderate',
      warnings_count: 0,
      cp_negative: false,
      top_two_alignment: 'strong',
    },
    ranking: {
      candidate_id: 'after',
      rank: 1,
      rank_score: 82,
      rank_tier: 'excellent',
      rank_breakdown: {
        preview_score_component: 30,
        composition_component: 15,
        buildability_component: 15,
        confidence_component: 12,
        alignment_component: 5,
        warning_penalty: 0,
        cp_penalty: 0,
        strategy_modifier: 5,
        total_score: 82,
        reasons: [],
      },
    },
    warnings: [],
    assumptions: ['Existing assumption', 'New assumption'],
    strategy: 'balanced',
    ...overrides,
  });
}

describe('Stage 5E comparison engine', () => {
  it('numericDelta marks improved worsened unchanged and unknown deterministically', () => {
    expect(numericDelta(1, 2).direction).toBe('improved');
    expect(numericDelta(2, 1).direction).toBe('worsened');
    expect(numericDelta(2, 2).direction).toBe('unchanged');
    expect(numericDelta(null, 2).direction).toBe('unknown');
    expect(numericDelta(2, 1, false).direction).toBe('improved');
  });

  it('detects added removed and facility count deltas', () => {
    const result = compareBuildSources(
      source({ placements: [placement('port_a', 'body1', 1, true), placement('support_z', 'body1', 2)] }),
      after(),
    );
    expect(result.added_facilities.map((item) => item.facility_template_id)).toContain('support_y');
    expect(result.removed_facilities.map((item) => item.facility_template_id)).toContain('support_z');
    expect(result.facility_count_deltas).toEqual(expect.arrayContaining([
      { facility_template_id: 'support_y', before_count: 0, after_count: 1, delta: 1 },
      { facility_template_id: 'support_z', before_count: 1, after_count: 0, delta: -1 },
    ]));
  });

  it('detects body assignment build order and primary port changes', () => {
    const result = compareBuildSources(
      source({ placements: [placement('port_a', 'body1', 1, true), placement('support_x', 'body1', 2)] }),
      after({ placements: [placement('port_a', 'body2', 2, true), placement('support_x', 'body1', 1, true)] }),
    );
    expect(result.changed_placements.some((item) => item.change_type === 'body_changed' && item.facility_template_id === 'port_a')).toBe(true);
    expect(result.changed_placements.some((item) => item.change_type === 'primary_port_changed' && item.facility_template_id === 'support_x')).toBe(true);
    expect(result.primary_port_change?.change_type).toBe('primary_port_changed');
  });

  it('detects order changes for identical facility/body placements', () => {
    const result = compareBuildSources(
      source({ placements: [placement('support_x', 'body1', 1)] }),
      after({ placements: [placement('support_x', 'body1', 3)] }),
    );
    expect(result.changed_placements).toEqual(expect.arrayContaining([
      expect.objectContaining({ facility_template_id: 'support_x', change_type: 'order_changed' }),
    ]));
  });

  it('handles duplicate facilities deterministically and does not mutate inputs', () => {
    const before = source({ placements: [placement('support_x', 'body1', 1), placement('support_x', 'body1', 2)] });
    const next = after({ placements: [placement('support_x', 'body1', 1), placement('support_x', 'body1', 3), placement('support_x', 'body1', 4)] });
    const beforeCopy = structuredClone(before.placements);
    const afterCopy = structuredClone(next.placements);
    const first = compareBuildSources(before, next);
    const second = compareBuildSources(before, next);
    expect(before.placements).toEqual(beforeCopy);
    expect(next.placements).toEqual(afterCopy);
    expect(first).toEqual(second);
    expect(first.added_facilities).toHaveLength(1);
    expect(first.changed_placements.some((item) => item.change_type === 'order_changed')).toBe(true);
  });

  it('computes preview summary and ranking deltas', () => {
    const result = compareBuildSources(source(), after());
    expect(result.preview_summary_delta.final_score.direction).toBe('improved');
    expect(result.preview_summary_delta.composition_score.direction).toBe('improved');
    expect(result.preview_summary_delta.buildability_score.direction).toBe('improved');
    expect(result.preview_summary_delta.confidence.direction).toBe('improved');
    expect(result.ranking_delta?.direction).toBe('improved');
    expect(result.ranking_delta?.before_rank).toBe(2);
    expect(result.ranking_delta?.after_rank).toBe(1);
  });

  it('handles missing preview summaries and current preview without ranking', () => {
    const result = compareBuildSources(
      source({ previewSummary: null, ranking: null, warnings: [], placements: [] }),
      after({ previewSummary: null, ranking: null, warnings: [], placements: [] }),
    );
    expect(result.preview_summary_delta.final_score.direction).toBe('unknown');
    expect(result.ranking_delta).toBeNull();
    expect(result.recommendation.verdict).toBe('insufficient_data');
  });

  it('reports warning and assumption set changes without duplicate noise', () => {
    expect(stringSetChanges([' Alpha ', 'Alpha', 'Beta'], ['Beta', 'Gamma'])).toEqual({
      before: ['Alpha', 'Beta'],
      after: ['Beta', 'Gamma'],
      added: ['Gamma'],
      removed: ['Alpha'],
      shared: ['Beta'],
    });
    const result = compareBuildSources(source(), after());
    expect(result.warning_changes.removed).toEqual(['Existing warning']);
    expect(result.assumption_changes.added).toEqual(['New assumption']);
  });

  it('computes risk deltas for warning count and CP risk changes', () => {
    const higherRisk = compareBuildSources(
      source({ warnings: [], previewSummary: { ...source().previewSummary!, warnings_count: 0, cp_negative: false } }),
      after({ warnings: ['New risk'], previewSummary: { ...after().previewSummary!, warnings_count: 2, cp_negative: true } }),
    );
    expect(higherRisk.risk_delta.warning_count.direction).toBe('worsened');
    expect(higherRisk.risk_delta.cp_negative_changed).toBe(true);
    expect(higherRisk.risk_delta.risk_direction).toBe('higher');
  });

  it('builds deterministic tradeoff summaries and conservative recommendations', () => {
    const improved = compareBuildSources(source(), after());
    expect(improved.tradeoff_summary).toContain('Changes target archetype from refinery_industrial to agriculture_terraforming.');
    expect(improved.tradeoff_summary.some((item) => item.includes('Improves final score'))).toBe(true);
    expect(improved.recommendation.verdict).toBe('prefer_after');

    const mixed = compareBuildSources(
      source(),
      after({ warnings: ['Risk'], previewSummary: { ...after().previewSummary!, cp_negative: true } }),
    );
    expect(mixed.recommendation.verdict).toBe('mixed');

    const noChange = compareBuildSources(
      source({ placements: [placement('support_x', 'body1', 1)], previewSummary: null, ranking: null, warnings: [] }),
      source({ id: 'same', label: 'Same', placements: [placement('support_x', 'body1', 1)], previewSummary: null, ranking: null, warnings: [] }),
    );
    expect(noChange.tradeoff_summary).toEqual(['No major placement changes detected.']);
  });

  it('creates copied sources from optimiser candidates and current preview placements', () => {
    const candidate: OptimiserCandidate = {
      candidate_id: 'candidate-a',
      label: 'Candidate A',
      target_archetype: 'agriculture_terraforming',
      strategy: 'balanced',
      placements: [
        { facility_template_id: 'support_x', local_body_id: 'body1', is_primary_port: true, build_order: 2 },
      ],
      rationale: [],
      warnings: ['Candidate warning'],
      assumptions: ['Candidate assumption'],
      tags: ['balanced'],
      preview_summary: after().previewSummary!,
    };
    const rank: RankedOptimiserCandidate = after().ranking!;
    const sourceFromCandidate = sourceFromOptimiserCandidate(candidate, rank);
    const previewPlacements = [placement('manual', 'body2', 1)];
    const sourceFromPreview = sourceFromCurrentPreview({ placements: previewPlacements, targetArchetype: 'trade_logistics' });

    expect(sourceFromCandidate.placements).toEqual([
      { facility_template_id: 'support_x', local_body_id: 'body1', is_primary_port: true, build_order: 1 },
    ]);
    expect(sourceFromCandidate.placements[0]).not.toBe(candidate.placements[0]);
    expect(sourceFromCandidate.ranking).toBe(rank);
    expect(sourceFromPreview.placements).toEqual(previewPlacements);
    expect(sourceFromPreview.placements[0]).not.toBe(previewPlacements[0]);
  });

  it('formats deltas verdicts risks and placement changes without React dependencies', () => {
    expect(formatDeltaValue({ before: 1, after: 2.5, delta: 1.5, direction: 'improved' }, ' pts')).toBe('+1.5 pts');
    expect(formatDeltaValue({ before: null, after: 2, delta: null, direction: 'unknown' })).toBe('unknown');
    expect(formatVerdictLabel('prefer_after')).toBe('Prefer after');
    expect(formatRiskDirection('higher')).toBe('Higher risk');
  });
});
