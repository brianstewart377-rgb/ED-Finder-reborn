import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import { SNAPSHOT_SOL_ID64, type CompareEntry } from '$lib/persistence/storage';
import { refreshCompareSnapshot } from './compare';

const provenance = {
  ranking_version: 'ranking-v1',
  ranking_sha256: 'a'.repeat(64),
  derived_generation_id: 'generation-2',
  publication_sequence: 2,
  archetype_version: 'archetypes-v1',
  archetype_manifest_sha256: 'b'.repeat(64),
  archetype_model_sha256: 'c'.repeat(64),
};

function serve(overrides: Record<string, unknown> = {}) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(
      JSON.stringify({
        id64: '9007199254740993',
        name: 'Refreshed system',
        coords: { x: 3, y: 4, z: 0 },
        distance_to_sol: 5,
        main_star_class: 'K',
        archetypes: { flexible: { score: 70, tier: 'B' } },
        primary_archetype: 'flexible',
        secondary_archetype: null,
        archetype_confidence: 0.8,
        overall_development_potential: 90,
        best_tier: 'A',
        facts: { body_count: 4, landable_count: 0, elw_count: null },
        ranking: provenance,
        ...overrides,
      }),
      { headers: { 'Content-Type': 'application/json' } },
    ),
  );
}

function previous(fields: Partial<CompareEntry> = {}): CompareEntry {
  return {
    id64: parseId64('9007199254740993'),
    name: 'Old system',
    captured_at: '2026-11-01T10:11:12Z',
    distance_reference: { kind: 'sol', id64: SNAPSHOT_SOL_ID64, name: 'Sol' },
    distance: 12,
    score_kind: 'selected_fit',
    selected_archetype: 'flexible',
    ranking_score: 60,
    population: 1000,
    security: 'High',
    purity_score: 50,
    ...fields,
  };
}

describe('comparison refresh facade', () => {
  afterEach(() => vi.restoreAllMocks());

  it('reads one published snapshot losslessly and keeps the selected mode with its API tier', async () => {
    const fetch = serve();
    const result = await refreshCompareSnapshot(previous());
    expect(fetch).toHaveBeenCalledOnce();
    expect((fetch.mock.calls[0][0] as Request).url).toContain(
      '/api/archetypes/system/9007199254740993',
    );
    expect(result).toMatchObject({
      id64: '9007199254740993',
      score_kind: 'selected_fit',
      selected_archetype: 'flexible',
      ranking_score: 70,
      ranking_tier: 'B',
      overall_development_potential: 90,
      best_tier: 'A',
      distance: 5,
      ranking_provenance: provenance,
      landable_count: 0,
      elw_count: null,
    });
    expect(result).not.toHaveProperty('population');
    expect(result).not.toHaveProperty('security');
    expect(result).not.toHaveProperty('purity_score');
    expect(result.captured_at).toBe(result.distance_captured_at);
  });

  it('keeps a non-Sol reference distance at its original capture time', async () => {
    serve();
    const reference = { kind: 'anchor', id64: parseId64('42'), name: 'Anchor' };
    const result = await refreshCompareSnapshot(
      previous({ distance_reference: reference }),
    );
    expect(result).toMatchObject({
      distance: 12,
      distance_reference: reference,
      distance_captured_at: '2026-11-01T10:11:12Z',
    });
    expect(result.captured_at).not.toBe(result.distance_captured_at);
  });

  it('does not invent a ranking mode or distance for older saves', async () => {
    serve();
    const result = await refreshCompareSnapshot(
      previous({
        score_kind: undefined,
        selected_archetype: undefined,
        distance_reference: undefined,
        distance: undefined,
      }),
    );
    expect(result).toMatchObject({
      score_kind: 'unknown',
      ranking_score: null,
      ranking_tier: null,
      distance_reference: null,
      distance: null,
    });
  });

  it('does not turn placeholder origin coordinates into zero distance', async () => {
    serve({ coords: { x: 0, y: 0, z: 0 }, distance_to_sol: 0 });
    expect(await refreshCompareSnapshot(previous())).toMatchObject({
      coords: { x: null, y: null, z: null },
      distance: null,
    });
  });
});
