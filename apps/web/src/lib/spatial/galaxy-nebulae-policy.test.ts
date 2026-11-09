import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { parseGalaxyNebulaeAsset } from './galaxy-nebulae';
import {
  galaxyNebulaVisibility,
  nebulaCloudEnabled,
  nebulaCloudRadiusLy,
  NEBULA_CLOUD_REENTER_LY,
  NEBULA_LANDMARK_CELL_LY,
  NEBULA_LANDMARK_FADE_END_LY,
  NEBULA_LANDMARK_LIMIT,
  NEBULA_SCHEMATIC_RADIUS_MIN_LY,
  NEBULA_SCHEMATIC_RADIUS_MAX_LY,
  PLANETARY_NEBULA_MARKER_RADIUS_LY,
  selectGalaxyNebulaLandmarks,
  type NebulaKind,
} from './galaxy-nebulae-policy';

function record(
  id: string,
  kind: NebulaKind = 'nebula',
  x = 0,
  name = id,
  regionName: string | null = null,
) {
  return {
    id,
    source: 'EDAstro Mapcharts',
    sourceId: id,
    name,
    systemName: id,
    regionName,
    regionId: null,
    kind,
    coordinates: [x, 0, 0],
    poiUrl: null,
  };
}

function parseRecords(nebulae: ReturnType<typeof record>[]) {
  return parseGalaxyNebulaeAsset({
    schemaVersion: 2,
    datasetId: 'nebula-policy-fixture',
    sourceUrl: 'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
    catalogueUrl: 'https://edastro.com/mapcharts/files.html',
    rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
    usageBasis: 'Non-commercial fixture',
    attribution: 'EDAstro / CMDR Orvidius',
    sourceLastModified: null,
    sourceByteCount: 123,
    sourceSha256: 'a'.repeat(64),
    count: nebulae.length,
    nebulae,
  });
}

describe('galaxy nebula landmark policy', () => {
  it('accounts for the complete pinned catalogue through the production parser', () => {
    const payload = parseGalaxyNebulaeAsset(
      JSON.parse(
        readFileSync(
          resolve(
            process.cwd(),
            'static/assets/edastro-mapcharts-nebulae.json',
          ),
          'utf8',
        ),
      ),
    );
    const selection = selectGalaxyNebulaLandmarks(payload);

    expect(payload.nebulae).toHaveLength(5_842);
    expect(selection.counts).toEqual({
      nebula: {
        catalogueRecordCount: 346,
        acceptedFeatureCount: 346,
        selectedLandmarkCount: 249,
        deduplicatedEntryCount: 97,
        cappedEntryCount: 0,
      },
      'planetary-nebula': {
        catalogueRecordCount: 5_496,
        acceptedFeatureCount: 5_496,
        selectedLandmarkCount: 5_496,
        deduplicatedEntryCount: 0,
        cappedEntryCount: 0,
      },
    });
    expect(selection.landmarks.nebula).toHaveLength(249);
    expect(selection.landmarks['planetary-nebula']).toHaveLength(5_496);
    expect(
      selectGalaxyNebulaLandmarks({
        ...payload,
        nebulae: [...payload.nebulae].reverse(),
      }),
    ).toEqual(selection);
  });

  it.each([
    {
      name: 'one diffuse and three planetary records',
      records: [
        record('diffuse'),
        record('planetary-a', 'planetary-nebula'),
        record('planetary-b', 'planetary-nebula'),
        record('planetary-c', 'planetary-nebula'),
      ],
      diffuse: 1,
      planetary: 3,
    },
    {
      name: 'planetary records only',
      records: [
        record('planetary-a', 'planetary-nebula'),
        record('planetary-b', 'planetary-nebula'),
        record('planetary-c', 'planetary-nebula'),
      ],
      diffuse: 0,
      planetary: 3,
    },
    { name: 'empty catalogue', records: [], diffuse: 0, planetary: 0 },
  ])('retains and counts $name', ({ records, diffuse, planetary }) => {
    const selection = selectGalaxyNebulaLandmarks(parseRecords(records));

    for (const [kind, count] of [
      ['nebula', diffuse],
      ['planetary-nebula', planetary],
    ] as const) {
      expect(selection.landmarks[kind]).toHaveLength(count);
      expect(selection.counts[kind]).toEqual({
        catalogueRecordCount: count,
        acceptedFeatureCount: count,
        selectedLandmarkCount: count,
        deduplicatedEntryCount: 0,
        cappedEntryCount: 0,
      });
    }
  });

  it('selects named, then regional, then lowest-ID landmarks independent of input order', () => {
    const records = [
      record('unnamed-low-id', 'nebula', 0, 'unnamed-low-id', 'Region'),
      record('named-high-id', 'nebula', 0, 'Named nebula'),
      record('named-regional-z', 'nebula', 1_400, 'Named nebula', 'Region'),
      record('named-unregional-a', 'nebula', 1_400, 'Named nebula'),
      record('regional-z', 'nebula', 2_800, 'regional-z', 'Region'),
      record('unregional-a', 'nebula', 2_800),
      record('Z', 'nebula', 4_200),
      record('a', 'nebula', 4_200),
    ];
    const payload = parseRecords(records);
    const selection = selectGalaxyNebulaLandmarks(payload);

    expect(selection.landmarks.nebula.map((nebula) => nebula.id)).toEqual([
      'named-regional-z',
      'named-high-id',
      'regional-z',
      'Z',
    ]);
    expect(selection.counts.nebula.deduplicatedEntryCount).toBe(4);
    expect(
      selectGalaxyNebulaLandmarks(parseRecords([...records].reverse())),
    ).toEqual(selection);
    expect(payload.nebulae.map((nebula) => nebula.id)).toEqual(
      records.map((nebula) => nebula.id),
    );
  });

  it('keeps the 1400 LY rounded cell and reports the 260 cap separately from duplicates', () => {
    expect(NEBULA_LANDMARK_CELL_LY).toBe(1_400);
    expect(NEBULA_LANDMARK_LIMIT).toBe(260);
    const records = Array.from({ length: 261 }, (_, index) =>
      record(
        `id-${index.toString().padStart(3, '0')}`,
        'nebula',
        index * 1_400,
      ),
    );
    records.push(record('duplicate', 'nebula', 699));
    const selection = selectGalaxyNebulaLandmarks(parseRecords(records));

    expect(selection.landmarks.nebula).toHaveLength(260);
    expect(selection.landmarks.nebula[0]?.id).toBe('duplicate');
    expect(selection.landmarks.nebula.at(-1)?.id).toBe('id-259');
    expect(selection.counts.nebula).toEqual({
      catalogueRecordCount: 262,
      acceptedFeatureCount: 262,
      selectedLandmarkCount: 260,
      deduplicatedEntryCount: 1,
      cappedEntryCount: 1,
    });
    expect(
      selectGalaxyNebulaLandmarks(parseRecords([...records].reverse())),
    ).toEqual(selection);
  });
});

describe('galaxy nebula zoom visibility', () => {
  it('keeps cloud hysteresis state through small zoom reversals', () => {
    expect(NEBULA_CLOUD_REENTER_LY).toBeGreaterThan(
      NEBULA_LANDMARK_FADE_END_LY,
    );
    let enabled = false;
    for (const [distance, expected] of [
      [8_000, true],
      [2_100, true],
      [2_000, false],
      [2_100, false],
      [1_990, false],
      [2_399, false],
      [2_400, true],
      [2_399, true],
      [2_001, true],
      [2_000, false],
    ] as const) {
      enabled = nebulaCloudEnabled(distance, enabled);
      expect(enabled).toBe(expected);
    }
  });

  it.each([NaN, Infinity, -Infinity, -1])(
    'does not enable clouds at invalid distance %s',
    (distance) => {
      expect(nebulaCloudEnabled(distance, false)).toBe(false);
      expect(nebulaCloudEnabled(distance, true)).toBe(false);
    },
  );

  it.each([
    [100_000, 1, 0],
    [8_000, 1, 0],
    [5_000, 0.5, 0],
    [4_000, 1 / 3, 0],
    [3_500, 0.25, 0.5],
    [3_000, 1 / 6, 1],
    [2_000, 0, 1],
    [100, 0, 1],
  ])(
    'at %d LY diffuse opacity is %f and planetary opacity is %f',
    (distance, diffuse, planetary) => {
      const visibility = galaxyNebulaVisibility(distance);
      expect(visibility.nebula).toBeCloseTo(diffuse);
      expect(visibility['planetary-nebula']).toBeCloseTo(planetary);
    },
  );

  it('gives close-range markers their own small radius', () => {
    expect(PLANETARY_NEBULA_MARKER_RADIUS_LY).toBe(3);
  });
});

describe('schematic nebula radius', () => {
  it('bounds every decorative radius to the documented range', () => {
    expect(NEBULA_SCHEMATIC_RADIUS_MIN_LY).toBe(600);
    expect(NEBULA_SCHEMATIC_RADIUS_MAX_LY).toBe(1_800);
    expect(nebulaCloudRadiusLy(0)).toBe(NEBULA_SCHEMATIC_RADIUS_MIN_LY);
    expect(nebulaCloudRadiusLy(1)).toBe(NEBULA_SCHEMATIC_RADIUS_MAX_LY);
    for (const hash of [
      -100,
      -1,
      0,
      0.1,
      0.5,
      0.9,
      1,
      2,
      100,
      NaN,
      Infinity,
      -Infinity,
    ]) {
      const radius = nebulaCloudRadiusLy(hash);
      expect(Number.isFinite(radius)).toBe(true);
      expect(radius).toBeGreaterThanOrEqual(NEBULA_SCHEMATIC_RADIUS_MIN_LY);
      expect(radius).toBeLessThanOrEqual(NEBULA_SCHEMATIC_RADIUS_MAX_LY);
      expect(nebulaCloudRadiusLy(hash)).toBe(radius);
    }
  });
});
