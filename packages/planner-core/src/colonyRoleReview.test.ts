import { describe, expect, it } from 'vitest';
import type { ObservedFact } from '@ed-finder/api-client/types';
import type { DeclaredColonyRole } from './colonyRoles';
import { buildObservedRolesFromFacts, buildRoleReview } from './colonyRoleReview';

const declaredIndustrial: DeclaredColonyRole = {
  id: 'declared:1:industrial_core',
  body_id: '1',
  role_id: 'industrial_core',
  source: 'declared',
  label: 'Industrial Core',
};

function fact(overrides: Partial<ObservedFact>): ObservedFact {
  return {
    observation_id: 'obs-1',
    system_id64: 123,
    created_at: '2026-05-19T00:00:00.000Z',
    updated_at: null,
    source: 'manual',
    fact_type: 'economy_presence',
    subject_type: 'body',
    subject_id: '1',
    local_body_id: '1',
    status: 'observed_present',
    confidence: 'high',
    tags: [],
    metadata: {},
    ...overrides,
  };
}

describe('colonyRoleReview', () => {
  it('renders aligned declared vs observed role review', () => {
    const observedRoles = buildObservedRolesFromFacts([
      fact({ economy: 'Industrial' }),
    ]);

    const review = buildRoleReview({
      declaredRoles: [declaredIndustrial],
      observedRoles,
    });

    expect(review.consistencyLabel).toBe('Strategy aligned');
    expect(review.summaries).toContain('Declared Industrial Core matches observed Observed Industrial Core.');
    expect(review.coverage.matchedCount).toBe(1);
  });

  it('renders conservative mismatch summaries', () => {
    const observedRoles = buildObservedRolesFromFacts([
      fact({ economy: 'Tourism' }),
    ]);

    const review = buildRoleReview({
      declaredRoles: [declaredIndustrial],
      observedRoles,
    });

    expect(review.consistencyLabel).toBe('Strategy diverging');
    expect(review.summaries).toContain('Declared Industrial Core but observed Observed Tourism Focus.');
    expect(review.coverage.mismatchCount).toBe(1);
  });

  it('reports insufficient observed evidence without converting inferred or declared roles', () => {
    const review = buildRoleReview({
      declaredRoles: [declaredIndustrial],
      observedRoles: [],
    });

    expect(review.consistencyLabel).toBe('Insufficient observed evidence');
    expect(review.summaries).toContain('No observed evidence recorded yet.');
    expect(review.observedRoles).toEqual([]);
    expect(review.declaredRoles).toEqual([declaredIndustrial]);
  });
});
