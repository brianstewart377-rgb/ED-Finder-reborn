import { describe, expect, it } from 'vitest';
import type { SimulateBuildResponse } from '@ed-finder/api-client/types';
import { buildPreviewResultGuidance } from './previewResultGuidance';

function result(overrides: Partial<SimulateBuildResponse> = {}): SimulateBuildResponse {
  return {
    system_id64: 123,
    mechanics_version: 'test',
    target_archetype: 'refinery_industrial',
    final_score: 80,
    composition_score: 80,
    buildability_score: 80,
    build_complexity: 'moderate',
    confidence: 0.75,
    cp: {
      yellow_cp_final: 0,
      green_cp_final: 0,
      yellow_cp_generated: 0,
      green_cp_generated: 0,
      yellow_cp_spent: 0,
      green_cp_spent: 0,
      t2_ports: 0,
      t3_ports: 0,
      warnings: [],
    },
    cp_timeline: [],
    cp_repair_suggestions: [],
    observation_summary: null,
    prediction_observation_diffs: [],
    economy_composition: {},
    economy_order: [],
    economy_stack: {},
    port_economy_states: [],
    influence_ledger: [],
    inherited_economies: [],
    topology: {},
    services: {},
    port_service_states: [],
    service_unlock_ledger: [],
    data_quality: {
      slots: 'estimated',
      facility_catalogue: 'community_observed',
      topology: 'inferred',
    },
    confidence_signals: [],
    mechanics_trace: {},
    top_two_alignment: 'none',
    contamination_risk: 'low',
    warnings: [],
    strengths: [],
    recommendations: [],
    mechanics_notes: [],
    links: { strong_links: [], weak_links: [] },
    ...overrides,
  } as SimulateBuildResponse;
}

describe('preview result guidance', () => {
  it('guides users to run Preview when no result exists', () => {
    const guidance = buildPreviewResultGuidance(null, false);
    expect(guidance.title).toBe('Preview not run yet');
    expect(guidance.items.join(' ')).toMatch(/Run Preview/);
  });

  it('warns when the Preview Result is stale', () => {
    const guidance = buildPreviewResultGuidance(result(), true);
    expect(guidance.title).toBe('Preview is stale');
    expect(guidance.items.join(' ')).toMatch(/Run Preview again/);
  });

  it('produces needs-work guidance for warnings or low buildability', () => {
    const guidance = buildPreviewResultGuidance(result({
      buildability_score: 40,
      warnings: ['CP pressure'],
    }), false);
    expect(guidance.title).toBe('Needs work');
    expect(guidance.items.join(' ')).toMatch(/generate Suggested Builds/);
    expect(guidance.items.join(' ')).not.toMatch(/viable/i);
  });

  it('produces needs-work guidance for a low final score', () => {
    const guidance = buildPreviewResultGuidance(result({ final_score: 40 }), false);
    expect(guidance.title).toBe('Needs work');
    expect(guidance.items.join(' ')).toMatch(/Final score is low/);
    expect(guidance.items.join(' ')).not.toMatch(/looks viable/i);
  });

  it('does not call a warning result viable', () => {
    const guidance = buildPreviewResultGuidance(result({ warnings: ['Service risk'] }), false);
    expect(guidance.title).toBe('Needs work');
    expect(guidance.items.join(' ')).toMatch(/warning/);
    expect(guidance.items.join(' ')).not.toMatch(/looks viable/i);
  });

  it('uses estimate wording for low-confidence-only results', () => {
    const guidance = buildPreviewResultGuidance(result({ confidence: 0.4 }), false);
    expect(guidance.title).toBe('Viable estimate with limited confidence');
    expect(guidance.items.join(' ')).toMatch(/estimate/);
    expect(guidance.items.join(' ')).not.toMatch(/optimal|guaranteed|correct/i);
  });

  it('produces viable guidance for strong results', () => {
    const guidance = buildPreviewResultGuidance(result(), false);
    expect(guidance.title).toBe('Looks viable');
    expect(guidance.items.join(' ')).toMatch(/Compare Suggested Builds/);
    expect(guidance.items.join(' ')).toMatch(/based on the current Preview Result/);
    expect(guidance.items.join(' ')).not.toMatch(/optimal|guaranteed|correct/i);
  });
});
