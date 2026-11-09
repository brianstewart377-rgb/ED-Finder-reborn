import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import { compare, pins } from '$lib/persistence/stores';
import {
  PERSISTENCE_KEYS,
  snapshotRankingIdentity,
} from '$lib/persistence/storage';
import {
  snapshotFromExplore,
  snapshotFromPin,
  snapshotFromWatchlist,
  toggleComparison,
  togglePin,
} from './shortlist';

const f4 = vi.hoisted(() => ({ enabled: false }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return f4.enabled;
  },
}));

const system = {
  id64: parseId64('9007199254740993'),
  name: 'Test system',
  coords: { x: 1, y: 2, z: 3 },
  distance: 12,
  primaryEconomy: 'Industrial',
  overall_development_potential: 81,
  elw_count: 2,
};

describe('Finder shortlist actions', () => {
  beforeEach(() => {
    f4.enabled = false;
    localStorage.clear();
    pins.set([]);
    compare.set([]);
  });

  it('pins full snapshots newest first and removes by exact Id64', () => {
    const first = snapshotFromExplore(system);
    expect(togglePin(pins, first)).toEqual({ selected: true, error: null });
    togglePin(pins, {
      ...first,
      id64: parseId64('9007199254740992'),
      name: 'Second',
    });
    const saved = get(pins).value;
    expect(saved.map((entry) => entry.name)).toEqual(['Second', 'Test system']);
    expect(saved[1]).toMatchObject({
      x: 1,
      y: 2,
      z: 3,
      economy: 'Industrial',
      archetype_score: 81,
    });
    expect(snapshotFromPin(saved[1])).toMatchObject({ ...first, elw_count: 2 });
    expect(togglePin(pins, first).selected).toBe(false);
    expect(get(pins).value.map((entry) => entry.id64)).toEqual([
      '9007199254740992',
    ]);
  });

  it('persists six comparison snapshots, rejects a seventh, and still removes at capacity', () => {
    for (let index = 1; index <= 6; index++) {
      expect(
        toggleComparison(compare, { ...system, id64: parseId64(String(index)) })
          .selected,
      ).toBe(true);
    }
    const before = localStorage.getItem(PERSISTENCE_KEYS.compare);
    expect(
      toggleComparison(compare, snapshotFromExplore(system)).error,
    ).toMatch(/6/);
    expect(localStorage.getItem(PERSISTENCE_KEYS.compare)).toBe(before);
    expect(
      toggleComparison(compare, { ...system, id64: parseId64('3') }).selected,
    ).toBe(false);
    expect(get(compare).value).toHaveLength(5);
    expect(get(compare).value[0]).toMatchObject({ elw_count: 2, distance: 12 });
  });

  it('adapts legacy pins and backend watchlist rows without inventing missing facts', () => {
    expect(
      snapshotFromPin({
        id64: system.id64,
        name: 'Legacy',
        pinned_at: '',
        x: 1,
        economy: 'Extraction',
      }),
    ).toMatchObject({
      id64: system.id64,
      name: 'Legacy',
      primaryEconomy: null,
      coords: { x: 1, y: null, z: null },
    });
    const snapshot = snapshotFromWatchlist({
      system_id64: system.id64,
      name: system.name,
      added_at: '2026-09-21T00:00:00Z',
      score: 30,
      archetype_score: 70,
      economy_suggestion: 'Agriculture',
    });
    expect(snapshot).toMatchObject({ id64: system.id64, archetype_score: 70 });
    expect(snapshot.primaryEconomy).toBeUndefined();
    expect(snapshot.elw_count).toBeUndefined();
  });

  it('records new capture times and preserves the original capture when copying a pin', () => {
    const capturedAt = '2026-11-01T10:11:12Z';
    const snapshot = snapshotFromPin({
      id64: system.id64,
      name: 'Older pin',
      pinned_at: capturedAt,
    });
    toggleComparison(compare, snapshot);
    expect(get(compare).value[0].captured_at).toBe(capturedAt);
    expect(get(compare).value[0].captured_at).not.toBe(
      new Date().toISOString(),
    );
    togglePin(pins, snapshotFromExplore(system));
    const capture = get(pins).value[0].captured_at;
    expect(typeof capture).toBe('string');
    expect(Number.isFinite(Date.parse(capture as string))).toBe(true);
    toggleComparison(compare, snapshot);
    toggleComparison(compare, snapshotFromExplore(system));
    expect(
      Number.isFinite(Date.parse(get(compare).value[0].captured_at as string)),
    ).toBe(true);
  });

  it('reports storage failures instead of claiming durable success', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota');
      });
    expect(
      toggleComparison(compare, snapshotFromExplore(system)).error,
    ).toMatch(/saved/);
    spy.mockRestore();
  });

  it.each([false, true])(
    'persists distinct overall and selected scores, API tiers, zero distance and provenance (F4=%s)',
    (enabled) => {
      f4.enabled = enabled;
      const provenance = {
        ranking_version: 'v3-colony-potential-2',
        ranking_sha256: 'ranking-sha',
        derived_generation_id: 'generation-id',
        publication_sequence: 1,
        archetype_version: 'v3-archetype-1',
        archetype_manifest_sha256: 'manifest-sha',
        archetype_model_sha256: 'model-sha',
      };
      const selected = snapshotFromExplore({
        ...system,
        archetype_score: null,
        overall_development_potential: 100,
        ranking_score: 75,
        ranking_tier: 'B',
        archetype_tier: null,
        distance: 0,
        // A future API best_tier belongs to the separate overall value only.
        ...{ best_tier: 'A' },
        score_kind: 'selected_fit',
        selected_archetype: 'flexible',
        distance_reference: {
          kind: 'sol',
          id64: parseId64('10477373803'),
          name: 'Sol',
        },
        ranking_provenance: provenance,
      });
      expect(selected).toMatchObject({
        archetype_score: 100,
        overall_development_potential: 100,
        ranking_score: 75,
        ranking_tier: 'B',
        archetype_tier: null,
        distance: 0,
        best_tier: 'A',
        score_kind: 'selected_fit',
        selected_archetype: 'flexible',
        distance_reference: { kind: 'sol', id64: '10477373803', name: 'Sol' },
        ranking_provenance: provenance,
      });
      expect(toggleComparison(compare, selected).selected).toBe(true);
      expect(get(compare).value[0]).toMatchObject({
        overall_development_potential: 100,
        ranking_score: 75,
        ranking_tier: 'B',
        score_kind: 'selected_fit',
        selected_archetype: 'flexible',
        distance: 0,
        distance_reference: { kind: 'sol', id64: '10477373803', name: 'Sol' },
        ranking_provenance: provenance,
      });
      expect(togglePin(pins, selected).selected).toBe(true);
      expect(get(pins).value[0]).toMatchObject({
        overall_development_potential: 100,
        ranking_score: 75,
        ranking_tier: 'B',
        distance: 0,
        distance_reference: { kind: 'sol', id64: '10477373803', name: 'Sol' },
        ranking_provenance: provenance,
      });
      compare.hydrate();
      pins.hydrate();
      expect(get(compare).value[0]).toEqual({
        ...selected,
        captured_at: expect.any(String),
      });
      expect(snapshotFromPin(get(pins).value[0])).toMatchObject(selected);
    },
  );

  it.each([false, true])(
    'keeps old pin and watchlist scores separate from known overall potential (F4=%s)',
    (enabled) => {
      f4.enabled = enabled;
      const legacyPin = {
        id64: system.id64,
        name: 'Older pin',
        pinned_at: '2026-09-21T00:00:00Z',
        archetype_score: 84,
        primary_archetype: 'agri_hub',
        distance: 0,
        notes: 'Keep this',
      };
      const fromPin = snapshotFromPin(legacyPin);
      const fromWatchlist = snapshotFromWatchlist({
        system_id64: system.id64,
        name: 'Older watchlist entry',
        score: 12,
        archetype_score: 84,
        primary_archetype: 'agri_hub',
      });
      expect(fromPin).toMatchObject(legacyPin);
      for (const snapshot of [fromPin, fromWatchlist]) {
        expect(snapshot.archetype_score).toBe(84);
        expect(snapshot.primary_archetype).toBe('agri_hub');
        expect(snapshotRankingIdentity(snapshot)).toMatchObject({
          score_kind: 'unknown',
          ranking_score: null,
          selected_archetype: null,
          distance_reference: null,
          ranking_provenance: null,
        });
        if (enabled) {
          expect(snapshot.overall_development_potential).toBeNull();
          expect(snapshot.score_kind).toBe('unknown');
        } else {
          expect(snapshot).not.toHaveProperty('overall_development_potential');
          expect(snapshot).not.toHaveProperty('score_kind');
        }
      }
    },
  );

  it('preserves the historical selected key when an older F4 pin is compared again', () => {
    f4.enabled = true;
    const snapshot = snapshotFromPin({
      id64: system.id64,
      name: 'Historical archetype',
      pinned_at: '',
      selected_archetype: 'agri_hub',
      ranking_score: 73,
      score_kind: 'selected_fit',
    });
    expect(toggleComparison(compare, snapshot).selected).toBe(true);
    expect(get(compare).value[0].selected_archetype).toBe('agri_hub');
    expect(
      snapshotRankingIdentity(get(compare).value[0]).selected_archetype,
    ).toBeNull();
  });

  it('records a pre-F4 row without identity as unknown mode with null provenance', () => {
    expect(snapshotFromExplore(system)).toMatchObject({
      archetype_score: 81,
      ranking_score: null,
      ranking_tier: null,
      score_kind: 'unknown',
      selected_archetype: null,
      distance_reference: null,
      ranking_provenance: null,
    });
  });
});
