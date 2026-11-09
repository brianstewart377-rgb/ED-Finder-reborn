import { describe, expect, it } from 'vitest';
import { normalizeAnySearchRow } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import type { CompareEntry } from '$lib/persistence/storage';
import { ARCHETYPES } from './archetypes';
import {
  buildCompareNotes,
  buildCompareRows,
  comparableOverallScores,
  compareCsv,
  COMPARE_MAX,
  snapshotDistance,
  snapshotCapturedAt,
  snapshotProvenanceSummary,
  snapshotScoreSummary,
} from './compare-metrics';
import { snapshotFromExplore } from './shortlist';

const entry = (
  id: string,
  fields: Record<string, unknown> = {},
): CompareEntry => ({
  id64: parseId64(id),
  name: `System ${id}`,
  ...fields,
});

describe('comparison snapshots', () => {
  it('does not replace an explicitly unknown capture time with the later pin time', () => {
    expect(
      snapshotCapturedAt(
        entry('1', {
          captured_at: null,
          pinned_at: '2026-11-02T12:00:00Z',
        }),
      ),
    ).toBeNull();
  });
  it('retains the V2 metric set and the six-system limit', () => {
    expect(COMPARE_MAX).toBe(6);
    expect(buildCompareRows([]).map((row) => row.label)).toEqual([
      'Captured at',
      'Development score',
      'Primary archetype',
      'Secondary archetype',
      'Archetype confidence',
      'Buildability',
      'Purity',
      'Estimated slots',
      'Primary economy',
      'Distance from ref',
      'Population',
      'Status',
      'Main star',
      'Security',
      'Allegiance',
      'ELW',
      'Water worlds',
      'Ammonia',
      'Terraformable',
      'Landable',
      'Bio signals',
      'Geo signals',
    ]);
  });

  it('compares known numbers, marks ties fairly, and prefers shorter distances', () => {
    const rows = buildCompareRows([
      entry('1', { archetype_score: 91, distance: 25, purity_score: 50 }),
      entry('2', {
        overall_development_potential: 91,
        distance: 12,
        purity_score: 50,
      }),
      entry('3', { archetype_score: 70, distance: 0 }),
    ]);
    expect(
      rows
        .find((row) => row.label === 'Development score')
        ?.cells.map((cell) => cell.best),
    ).toEqual([true, true, false]);
    expect(
      rows
        .find((row) => row.label === 'Distance from ref')
        ?.cells.map((cell) => cell.best),
    ).toEqual([false, true, false]);
    expect(
      rows
        .find((row) => row.label === 'Purity')
        ?.cells.every((cell) => !cell.best),
    ).toBe(true);
  });

  it('does not invent unknown data or turn invalid values into winners', () => {
    const rows = buildCompareRows([
      entry('1', {
        population: null,
        distance: 0,
        elw_count: 0,
        purity_score: '90',
        is_colonised: false,
      }),
      entry('2', {
        population: 0,
        distance: NaN,
        purity_score: 80,
        is_colonised: true,
      }),
    ]);
    const cells = (label: string) =>
      rows.find((row) => row.label === label)!.cells;
    expect(cells('Population').map((cell) => cell.display)).toEqual([
      'Unknown',
      'Population unknown',
    ]);
    expect(cells('Distance from ref').map((cell) => cell.display)).toEqual([
      'Unknown',
      'Unknown',
    ]);
    expect(cells('ELW').map((cell) => cell.display)).toEqual(['0', 'Unknown']);
    expect(cells('Purity').every((cell) => !cell.best)).toBe(true);
    expect(
      buildCompareRows([entry('3')]).find((row) => row.label === 'Status')
        ?.cells[0].display,
    ).toBe('Unknown');
  });

  it('exports the displayed metrics with CSV escaping and safe spreadsheet text', () => {
    const csv = compareCsv([
      entry('18446744073709551615', {
        name: '=HYPERLINK("bad")',
        archetype_score: 91,
        primary_archetype: 'refinery_industrial',
      }),
    ]);
    expect(csv).toContain('"Metric","\'=HYPERLINK(""bad"")"');
    expect(csv).toContain('"Development score","91"');
    expect(csv).toContain(
      '"Primary archetype","Refinery / Industrial Megacomplex"',
    );
    expect(csv).toContain('"Landable","Unknown"');
  });

  it('keeps explicit flag-off rows and CSV identical to the released defaults', () => {
    const entries = [
      entry('1', { distance: 12, archetype_score: 88 }),
      entry('2', { distance: 20, archetype_score: 76 }),
    ];
    const options = { finderF4Enabled: false };
    expect(buildCompareRows(entries, options)).toEqual(
      buildCompareRows(entries),
    );
    expect(compareCsv(entries, options)).toEqual(compareCsv(entries));
    expect(buildCompareNotes(entries, options)).toEqual([]);
    expect(compareCsv(entries, options)).toContain(
      '"Development score","88","76"',
    );
    expect(compareCsv(entries)).toContain('"Distance from ref LY","12","20"');
  });
});

const f4 = { finderF4Enabled: true };
const provenance = {
  ranking_version: 'v1',
  ranking_sha256: 'ranking-hash',
  derived_generation_id: 'generation-a',
  publication_sequence: 1,
  archetype_version: 'archetypes-v1',
  archetype_manifest_sha256: 'manifest-a',
  archetype_model_sha256: 'model-hash',
};
const sol = { kind: 'sol', id64: parseId64('10477373803'), name: 'Sol' };
const modern = (id: string, fields: Record<string, unknown> = {}) =>
  entry(id, {
    score_kind: 'overall_potential',
    overall_development_potential: 80,
    ranking_score: 80,
    ranking_tier: 'S',
    ranking_provenance: provenance,
    selected_archetype: null,
    distance_reference: sol,
    distance: 10,
    ...fields,
  });
const metric = (entries: CompareEntry[], label: string) =>
  buildCompareRows(entries, f4).find((row) => row.label === label);

describe('F4 mode- and reference-safe comparison', () => {
  it('uses every canonical archetype label and leaves historical keys readable', () => {
    const rows = buildCompareRows(
      [
        ...ARCHETYPES.map(({ key }, index) =>
          modern(String(index + 1), { primary_archetype: key }),
        ),
        entry('99', {
          primary_archetype: 'refinery_industrial',
          secondary_archetype: 'unknown_historical_key',
        }),
      ],
      f4,
    );
    expect(
      rows
        .find((row) => row.label === 'Primary archetype')
        ?.cells.map((cell) => cell.display),
    ).toEqual([...ARCHETYPES.map(({ label }) => label), 'refinery industrial']);
    expect(
      rows.find((row) => row.label === 'Secondary archetype')?.cells.at(-1)
        ?.display,
    ).toBe('unknown historical key');
  });

  it('compares overall potential across modes without substituting the selected fit', () => {
    const entries = [
      modern('1', { overall_development_potential: 70, ranking_score: 70 }),
      modern('2', {
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        overall_development_potential: 90,
        ranking_score: 65,
      }),
    ];
    expect(metric(entries, 'Best Colony Potential')?.cells).toEqual([
      { display: '70 · Tier S', csvValue: 70, best: false },
      { display: '90', csvValue: 90, best: true },
    ]);
    expect(metric(entries, 'Paradise World fit')).toBeUndefined();
    expect(buildCompareNotes(entries, f4)).toContain(
      'Selected-fit comparison omitted: different ranking modes.',
    );
    expect(compareCsv(entries, f4)).toContain(
      '"Best Colony Potential","70","90"',
    );
    expect(compareCsv(entries, f4)).not.toContain('Paradise World fit');
  });

  it('compares selected fits only for the same canonical archetype', () => {
    const selected = {
      score_kind: 'selected_fit',
      selected_archetype: 'flexible',
    };
    const entries = [
      modern('1', { ...selected, ranking_score: 60, ranking_tier: 'B' }),
      modern('2', { ...selected, ranking_score: 90, ranking_tier: 'S' }),
    ];
    expect(metric(entries, 'Flexible Multi-Role Colony fit')?.cells).toEqual([
      { display: '60 · Tier B', csvValue: 60, best: false },
      { display: '90 · Tier S', csvValue: 90, best: true },
    ]);
    for (const key of ['paradise', 'historical_key', null]) {
      const mixed = [entries[0], { ...entries[1], selected_archetype: key }];
      expect(
        buildCompareRows(mixed, f4).some((row) => row.label.endsWith(' fit')),
      ).toBe(false);
      expect(metric(mixed, 'Best Colony Potential')).toBeDefined();
    }
    const historical = entries.map((entry) => ({
      ...entry,
      selected_archetype: 'historical_key',
    }));
    expect(
      buildCompareRows(historical, f4).some((row) =>
        row.label.endsWith(' fit'),
      ),
    ).toBe(false);
  });

  it('renders only the corresponding API tier, never a score-derived or selected overall tier', () => {
    const entries = [
      modern('1', {
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        overall_development_potential: 99,
        best_tier: 'D',
        ranking_score: 90,
        ranking_tier: 'C',
      }),
      modern('2', {
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        overall_development_potential: 98,
        ranking_score: 89,
        ranking_tier: null,
      }),
      modern('3', {
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        overall_development_potential: 97,
        best_tier: 'invalid',
        ranking_score: 88,
        ranking_tier: 'invalid',
      }),
    ];
    expect(
      metric(entries, 'Best Colony Potential')?.cells.map(
        (cell) => cell.display,
      ),
    ).toEqual(['99 · Tier D', '98', '97']);
    expect(
      metric(entries, 'Paradise World fit')?.cells.map((cell) => cell.display),
    ).toEqual(['90 · Tier C', '89', '88']);
    expect(
      metric([modern('4', { ranking_tier: null })], 'Best Colony Potential')
        ?.cells[0].display,
    ).toBe('80');
  });

  it.each(['S', 'A', 'B', 'C', 'D'] as const)(
    'retains API Tier %s in Any-mode snapshot comparisons and summaries',
    (tier) => {
      const snapshot = snapshotFromExplore(
        normalizeAnySearchRow(
          {
            id64: '1' as unknown as number,
            name: 'Any-mode system',
            overall_development_potential: 99,
            archetype_score: 99,
            archetype_tier: tier,
          },
          null,
          provenance,
        ),
      );
      expect(snapshot).not.toHaveProperty('best_tier');
      expect(
        metric([snapshot], 'Best Colony Potential')?.cells[0].display,
      ).toBe(`99 · Tier ${tier}`);
      expect(snapshotScoreSummary(snapshot)).toBe(
        `Best Colony Potential 99 · Tier ${tier}`,
      );
    },
  );

  it.each([undefined, 'D'] as const)(
    'keeps the selected-fit tier separate from overall best_tier %s',
    (bestTier) => {
      const snapshot = snapshotFromExplore({
        id64: parseId64('1'),
        name: 'Selected-fit system',
        overall_development_potential: 99,
        ranking_score: 90,
        ranking_tier: 'C',
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        ranking_provenance: provenance,
        ...(bestTier ? { best_tier: bestTier } : {}),
      });
      const overall = bestTier ? '99 · Tier D' : '99';
      expect(
        metric([snapshot], 'Best Colony Potential')?.cells[0].display,
      ).toBe(overall);
      expect(metric([snapshot], 'Paradise World fit')?.cells[0].display).toBe(
        '90 · Tier C',
      );
      expect(snapshotScoreSummary(snapshot)).toBe(
        `Best Colony Potential ${overall}; Paradise World fit 90 · Tier C`,
      );
    },
  );

  it.each([
    'ranking_version',
    'ranking_sha256',
    'archetype_version',
    'archetype_model_sha256',
  ])(
    'omits every ranked score comparison when %s differs or is missing',
    (field) => {
      for (const value of ['different', undefined, '']) {
        const selected = {
          score_kind: 'selected_fit',
          selected_archetype: 'paradise',
        };
        const entries = [
          modern('1', selected),
          modern('2', {
            ...selected,
            ranking_provenance: { ...provenance, [field]: value },
          }),
        ];
        expect(comparableOverallScores(entries)).toBe(false);
        expect(metric(entries, 'Best Colony Potential')).toBeUndefined();
        expect(metric(entries, 'Paradise World fit')).toBeUndefined();
        expect(buildCompareNotes(entries, f4).join(' ')).toContain(
          'different ranking versions',
        );
      }
    },
  );

  it('permits identical semantics across generations and exposes all freshness identities', () => {
    const selected = {
      score_kind: 'selected_fit',
      selected_archetype: 'paradise',
    };
    const next = modern('2', {
      ...selected,
      ranking_provenance: {
        ...provenance,
        derived_generation_id: 'generation-b',
        publication_sequence: 2,
        archetype_manifest_sha256: 'manifest-b',
      },
    });
    const entries = [modern('1', selected), next];
    expect(metric(entries, 'Best Colony Potential')).toBeDefined();
    expect(metric(entries, 'Paradise World fit')).toBeDefined();
    expect(snapshotProvenanceSummary(next)).toBe(
      'Ranked on generation generation-b; publication 2; archetype manifest manifest-b',
    );
    expect(buildCompareNotes(entries, f4)).toEqual([]);
  });

  it('retains older stored values without guessing a comparable mode or reference', () => {
    const legacy = entry('1', { archetype_score: 91, distance: 0 });
    const entries = [legacy, modern('2')];
    expect(metric(entries, 'Best Colony Potential')).toBeUndefined();
    expect(
      buildCompareRows(entries, f4).some((row) =>
        row.label.startsWith('Distance'),
      ),
    ).toBe(false);
    expect(snapshotScoreSummary(legacy)).toBe(
      'Stored score 91; Older save: ranking mode or provenance unknown',
    );
    expect(snapshotProvenanceSummary(legacy)).toBeNull();
    expect(snapshotDistance(legacy)).toBeNull();
    expect(buildCompareNotes(entries, f4).join(' ')).toContain('Older save');
    expect(legacy).not.toHaveProperty('score_kind');
  });

  it('does not compare an unknown mode even if ranking provenance survived', () => {
    const unknown = modern('1', { score_kind: 'unknown' });
    expect(comparableOverallScores([unknown, modern('2')])).toBe(false);
    expect(
      metric([unknown, modern('2')], 'Best Colony Potential'),
    ).toBeUndefined();
    expect(buildCompareNotes([unknown], f4)).toContain(
      'Score comparison omitted: ranking mode unknown.',
    );
  });

  it('ranks referenced zero against positive distances, in table and CSV', () => {
    const entries = [
      modern('1', { distance: 0 }),
      modern('2', { distance: 2.5 }),
    ];
    expect(metric(entries, 'Distance from Sol')?.cells).toEqual([
      { display: '0.00 LY', csvValue: 0, best: true },
      { display: '2.50 LY', csvValue: 2.5, best: false },
    ]);
    expect(compareCsv(entries, f4)).toContain(
      '"Distance from Sol LY","0","2.5"',
    );
    const legacyZero = [...entries, entry('3', { distance: 0 })];
    expect(metric(legacyZero, 'Distance from Sol')).toBeUndefined();
    expect(compareCsv(legacyZero, f4)).not.toContain('Distance from');
  });

  it('compares a selected Sol distance and an Any Sol anchor using their exact normalized origin', () => {
    const entries = [
      modern('1', {
        score_kind: 'selected_fit',
        selected_archetype: 'paradise',
        distance: 0,
      }),
      modern('2', {
        distance_reference: { ...sol, kind: 'anchor', name: 'Sol anchor' },
      }),
    ];
    expect(metric(entries, 'Distance from Sol')?.cells[0].best).toBe(true);
  });

  it('labels an exact named anchor and excludes mixed, unknown or invalid references', () => {
    const anchor = { kind: 'anchor', id64: parseId64('50'), name: 'Achenar' };
    const first = modern('1', { distance_reference: anchor });
    expect(
      metric(
        [first, modern('2', { distance_reference: anchor })],
        'Distance from Achenar',
      ),
    ).toBeDefined();
    for (const reference of [
      sol,
      null,
      { ...anchor, id64: '51' },
      { ...anchor, kind: 'sol' },
      { ...anchor, kind: 'unknown' },
    ]) {
      const entries = [first, modern('2', { distance_reference: reference })];
      expect(
        buildCompareRows(entries, f4).some((row) =>
          row.label.startsWith('Distance'),
        ),
      ).toBe(false);
      expect(buildCompareNotes(entries, f4).join(' ')).toContain(
        'different or unknown distance references',
      );
    }
  });

  it('uses only finite measured distances and accepts every finite value with a reference', () => {
    const entries = [
      modern('1', { distance: NaN }),
      modern('2', { distance: Infinity }),
      modern('3', { distance: -1 }),
      modern('4', { distance: 0 }),
    ];
    expect(
      metric(entries, 'Distance from Sol')?.cells.map((cell) => cell.display),
    ).toEqual(['Unknown', 'Unknown', '-1.00 LY', '0.00 LY']);
    expect(
      metric(entries, 'Distance from Sol')?.cells.map((cell) => cell.best),
    ).toEqual([false, false, true, false]);
    expect(buildCompareRows([], f4)).not.toContainEqual(
      expect.objectContaining({ label: 'Best Colony Potential' }),
    );
    expect(buildCompareNotes([], f4)).toEqual([]);
  });
});
