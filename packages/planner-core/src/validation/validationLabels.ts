/**
 * User-facing labels and copy for Stage 6D Validation rendering.
 *
 * Stage 6D renders the Stage 6C `/api/observations/compare` response
 * inside Colony Planner. The labels here are intentionally conservative:
 *
 *   * "contradicted" is rendered as **Needs review**, never "wrong".
 *   * "predicted_only" and "observed_only" use neutral wording that
 *     describes the asymmetry without implying the prediction is
 *     incorrect or that the observation is proof.
 *   * The advisory copy at the top makes the boundary explicit so users
 *     do not read Validation as a scoring/ranking input.
 *
 * No copy in this module classifies a prediction as proven, corrected,
 * or wrong. Stage 6D is a display layer only; mechanics/rule mutation
 * and any confidence-feedback loop are deferred to Stage 6E.
 */
import type {
  ComparisonConfidenceImpact,
  ComparisonOverallStatus,
  ComparisonSeverity,
  ComparisonStatus,
  ValidationReviewArea,
  ValidationReviewStatus,
} from '@ed-finder/api-client/types';

export const ADVISORY_COPY =
  'Later step: Validation compares prediction with recorded Observed Evidence. It does not change scoring, Suggested Builds, generated plans, or in-game state.';

export const NO_PREVIEW_COPY =
  'Run Preview first, then record Observed Evidence after checking in-game. Validation compares that evidence with the current Preview Result.';

export const STALE_PREVIEW_COPY =
  'Preview result is stale. The Build Plan has changed since this preview was run, so this validation may reflect the previous preview result. Run Preview again before relying on validation.';

export const EMPTY_COMPARISONS_COPY =
  'No comparison rows yet. Run Preview and record observed evidence for this system to populate validation.';

export const REVIEW_ADVISORY_COPY =
  'Review guidance is advisory. This does not change mechanics or scoring. Use this to decide what to investigate next.';

export const VALIDATION_REVIEW_REMINDERS = [
  'Preview assumes current plan; confirm in game.',
  'Architect primary-port flag has not been recorded as a dedicated validation field; check System Map -> Architect Mode before final major station placement.',
] as const;

export const PREDICTED_ONLY_COPY =
  'Predicted, but no matching observation has been recorded yet.';

export const OBSERVED_ONLY_COPY =
  'Observed evidence exists, but the current prediction has no matching item.';

export const OVERALL_STATUS_LABELS: Record<ComparisonOverallStatus, string> = {
  no_observations: 'No observations yet',
  confirmed: 'Mostly confirmed',
  mixed: 'Mixed evidence',
  needs_review: 'Needs review',
  insufficient_evidence: 'Insufficient evidence',
};

export const CONFIDENCE_IMPACT_LABELS: Record<ComparisonConfidenceImpact, string> = {
  none: 'No impact',
  strengthened: 'Strengthened',
  weakened: 'Weakened',
  mixed: 'Mixed',
  insufficient_evidence: 'Insufficient evidence',
};

/**
 * Per-row status labels. The "contradicted" mapping is deliberate: the
 * spec for Stage 6D forbids the word "wrong" and asks for **Needs
 * review** so users understand it as something to investigate, not a
 * verdict.
 */
export const COMPARISON_STATUS_LABELS: Record<ComparisonStatus, string> = {
  confirmed: 'Confirmed',
  contradicted: 'Needs review',
  predicted_only: 'Predicted only',
  observed_only: 'Observed only',
  unknown: 'Unknown',
  unverified: 'Unverified',
};

export const COMPARISON_STATUSES: readonly ComparisonStatus[] = [
  'confirmed',
  'contradicted',
  'predicted_only',
  'observed_only',
  'unknown',
  'unverified',
];

export const COMPARISON_SEVERITY_LABELS: Record<ComparisonSeverity, string> = {
  info: 'Info',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};

export const REVIEW_STATUS_LABELS: Record<ValidationReviewStatus, string> = {
  no_action: 'No action',
  monitor: 'Monitor',
  review_recommended: 'Review recommended',
  review_high_priority: 'High-priority review',
  insufficient_evidence: 'Insufficient evidence',
  mixed_evidence: 'Mixed evidence',
};

export const REVIEW_AREA_LABELS: Record<ValidationReviewArea, string> = {
  service_rules: 'Service rules',
  economy_rules: 'Economy rules',
  cp_rules: 'CP rules',
  facility_rules: 'Facility rules',
  build_outcome: 'Build outcome',
  prediction_claims: 'Prediction claims',
  evidence_quality: 'Evidence quality',
  general: 'General',
};

export function overallStatusLabel(value: string): string {
  return (OVERALL_STATUS_LABELS as Record<string, string>)[value] ?? value;
}

export function confidenceImpactLabel(value: string): string {
  return (CONFIDENCE_IMPACT_LABELS as Record<string, string>)[value] ?? value;
}

export function comparisonStatusLabel(value: string): string {
  return (COMPARISON_STATUS_LABELS as Record<string, string>)[value] ?? value;
}

export function comparisonSeverityLabel(value: string): string {
  return (COMPARISON_SEVERITY_LABELS as Record<string, string>)[value] ?? value;
}

export function reviewStatusLabel(value: string): string {
  return (REVIEW_STATUS_LABELS as Record<string, string>)[value] ?? value;
}

export function reviewAreaLabel(value: string): string {
  return (REVIEW_AREA_LABELS as Record<string, string>)[value] ?? value;
}
