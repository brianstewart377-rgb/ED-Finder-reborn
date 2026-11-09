import type {
  OptimiserCandidate,
  OptimiserCandidatePreviewSummary,
  RankedOptimiserCandidate,
  SimulateBuildPlacement,
} from '@ed-finder/api-client/types';
import { candidatePlacementsToPreviewPlacements } from '../optimiserUtils';

export type ComparisonSourceKind =
  | 'current_preview'
  | 'optimiser_candidate'
  | 'loaded_optimiser_candidate'
  | 'manual_plan';

export interface BuildComparisonSource {
  id: string;
  label: string;
  kind: ComparisonSourceKind;
  targetArchetype?: string | null;
  placements: SimulateBuildPlacement[];
  previewSummary?: OptimiserCandidatePreviewSummary | null;
  ranking?: RankedOptimiserCandidate | null;
  warnings?: string[];
  assumptions?: string[];
  tags?: string[];
  strategy?: string | null;
}

export type PlacementChangeType =
  | 'added'
  | 'removed'
  | 'body_changed'
  | 'order_changed'
  | 'primary_port_changed'
  | 'unchanged';

export interface PlacementIdentity {
  facility_template_id: string;
  local_body_id?: string | null;
}

export interface FacilityCountDelta {
  facility_template_id: string;
  before_count: number;
  after_count: number;
  delta: number;
}

export interface PlacementChange {
  facility_template_id: string;
  before_body_id?: string | null;
  after_body_id?: string | null;
  before_build_order?: number | null;
  after_build_order?: number | null;
  before_primary_port?: boolean | null;
  after_primary_port?: boolean | null;
  change_type: PlacementChangeType;
}

export interface NumericDelta {
  before?: number | null;
  after?: number | null;
  delta?: number | null;
  direction: 'improved' | 'worsened' | 'unchanged' | 'unknown';
}

export interface ComparisonRiskDelta {
  warning_count: NumericDelta;
  cp_negative_changed: boolean;
  before_cp_negative?: boolean | null;
  after_cp_negative?: boolean | null;
  risk_direction: 'lower' | 'higher' | 'unchanged' | 'unknown';
}

export interface ComparisonRankingDelta {
  before_rank?: number | null;
  after_rank?: number | null;
  before_rank_score?: number | null;
  after_rank_score?: number | null;
  rank_score_delta?: number | null;
  before_rank_tier?: string | null;
  after_rank_tier?: string | null;
  direction: 'improved' | 'worsened' | 'unchanged' | 'unknown';
}

export interface StringSetChanges {
  before: string[];
  after: string[];
  added: string[];
  removed: string[];
  shared: string[];
}

export interface BuildComparisonResult {
  before: {
    id: string;
    label: string;
    kind: ComparisonSourceKind;
    targetArchetype?: string | null;
  };
  after: {
    id: string;
    label: string;
    kind: ComparisonSourceKind;
    targetArchetype?: string | null;
  };
  target_archetype_changed: boolean;
  before_target_archetype?: string | null;
  after_target_archetype?: string | null;
  facility_count_deltas: FacilityCountDelta[];
  added_facilities: PlacementChange[];
  removed_facilities: PlacementChange[];
  changed_placements: PlacementChange[];
  unchanged_placements_count: number;
  primary_port_change?: PlacementChange | null;
  preview_summary_delta: {
    final_score: NumericDelta;
    composition_score: NumericDelta;
    buildability_score: NumericDelta;
    confidence: NumericDelta;
  };
  ranking_delta?: ComparisonRankingDelta | null;
  risk_delta: ComparisonRiskDelta;
  warning_changes: StringSetChanges;
  assumption_changes: StringSetChanges;
  tradeoff_summary: string[];
  recommendation: {
    verdict: 'prefer_after' | 'prefer_before' | 'mixed' | 'insufficient_data';
    reasons: string[];
  };
}

export function sourceFromOptimiserCandidate(
  candidate: OptimiserCandidate,
  ranking?: RankedOptimiserCandidate | null,
  kind: ComparisonSourceKind = 'optimiser_candidate',
): BuildComparisonSource {
  return {
    id: candidate.candidate_id,
    label: candidate.label,
    kind,
    targetArchetype: candidate.target_archetype,
    placements: candidatePlacementsToPreviewPlacements(candidate.placements),
    previewSummary: candidate.preview_summary ?? null,
    ranking: ranking ?? null,
    warnings: [...candidate.warnings],
    assumptions: [...candidate.assumptions],
    tags: [...candidate.tags],
    strategy: candidate.strategy,
  };
}

export function sourceFromCurrentPreview({
  id = 'current-preview',
  label = 'Current Build Plan',
  targetArchetype,
  placements,
  previewSummary = null,
  warnings = [],
  assumptions = [],
  kind = 'current_preview',
}: {
  id?: string;
  label?: string;
  targetArchetype?: string | null;
  placements: SimulateBuildPlacement[];
  previewSummary?: OptimiserCandidatePreviewSummary | null;
  warnings?: string[];
  assumptions?: string[];
  kind?: ComparisonSourceKind;
}): BuildComparisonSource {
  return {
    id,
    label,
    kind,
    targetArchetype,
    placements: placements.map((placement) => ({ ...placement })),
    previewSummary,
    ranking: null,
    warnings: [...warnings],
    assumptions: [...assumptions],
    tags: [],
    strategy: null,
  };
}
