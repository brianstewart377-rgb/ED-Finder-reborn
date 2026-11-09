import type { PredictionObservationComparison } from '@ed-finder/api-client/types';

export type ValidationMismatchCategory =
  | 'matches_plan'
  | 'differs_from_plan'
  | 'missing_observation'
  | 'unknown_not_checked'
  | 'needs_manual_review';

interface ValidationMismatchCategoryCopy {
  label: string;
  description: string;
  tone: 'confirmed' | 'warning' | 'missing' | 'unknown' | 'review';
}

const CATEGORY_COPY: Record<ValidationMismatchCategory, ValidationMismatchCategoryCopy> = {
  matches_plan: {
    label: 'Matches plan',
    description: 'Observed evidence matches the Preview Result for this row.',
    tone: 'confirmed',
  },
  differs_from_plan: {
    label: 'Differs from plan',
    description: 'Observed value differs from preview.',
    tone: 'warning',
  },
  missing_observation: {
    label: 'Missing observation',
    description: 'Preview expects this value, but no matching observation has been recorded.',
    tone: 'missing',
  },
  unknown_not_checked: {
    label: 'Unknown / not checked',
    description: 'Validation cannot compare this row yet; confirm it in-game when it matters.',
    tone: 'unknown',
  },
  needs_manual_review: {
    label: 'Needs manual review',
    description: 'Evidence and preview do not line up cleanly enough for automatic review.',
    tone: 'review',
  },
};

export function validationMismatchCategory(
  comparison: Pick<PredictionObservationComparison, 'status'>,
): ValidationMismatchCategory {
  switch (comparison.status) {
    case 'confirmed':
      return 'matches_plan';
    case 'contradicted':
      return 'differs_from_plan';
    case 'predicted_only':
      return 'missing_observation';
    case 'unknown':
      return 'unknown_not_checked';
    case 'observed_only':
    case 'unverified':
      return 'needs_manual_review';
    default:
      return 'needs_manual_review';
  }
}

export function validationMismatchCategoryCopy(
  category: ValidationMismatchCategory,
): ValidationMismatchCategoryCopy {
  return CATEGORY_COPY[category];
}

export function validationMismatchCategoryClassName(
  category: ValidationMismatchCategory,
): string {
  const tone = CATEGORY_COPY[category].tone;
  if (tone === 'confirmed') {
    return 'border-green/30 bg-green/10 text-green';
  }
  if (tone === 'warning') {
    return 'border-orange/35 bg-orange/10 text-orange';
  }
  if (tone === 'missing') {
    return 'border-cyan/30 bg-cyan/10 text-cyan';
  }
  if (tone === 'unknown') {
    return 'border-border bg-bg3 text-silver-dk';
  }
  return 'border-orange/25 bg-bg3/60 text-silver';
}
