import { describe, expect, it } from 'vitest';
import {
  validationMismatchCategory,
  validationMismatchCategoryCopy,
} from './validationReviewCategoryUtils';

describe('validationReviewCategoryUtils', () => {
  it.each([
    ['confirmed', 'matches_plan', 'Matches plan'],
    ['contradicted', 'differs_from_plan', 'Differs from plan'],
    ['predicted_only', 'missing_observation', 'Missing observation'],
    ['unknown', 'unknown_not_checked', 'Unknown / not checked'],
    ['observed_only', 'needs_manual_review', 'Needs manual review'],
    ['unverified', 'needs_manual_review', 'Needs manual review'],
    ['future_status', 'needs_manual_review', 'Needs manual review'],
  ])('maps %s into the Stage 14B review category', (status, category, label) => {
    const mapped = validationMismatchCategory({ status });
    expect(mapped).toBe(category);
    expect(validationMismatchCategoryCopy(mapped).label).toBe(label);
  });

  it('uses explicit mismatch copy for differs-from-plan rows', () => {
    const category = validationMismatchCategory({ status: 'contradicted' });
    expect(validationMismatchCategoryCopy(category).description).toBe(
      'Observed value differs from preview.',
    );
  });
});
