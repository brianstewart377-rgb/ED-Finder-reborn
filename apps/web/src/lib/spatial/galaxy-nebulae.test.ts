import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  createGalaxyNebulaeContribution,
  parseGalaxyNebulaeAsset,
} from './galaxy-nebulae';

const asset = {
  schemaVersion: 2,
  datasetId: 'edastro-mapcharts-nebulae-coordinates',
  sourceUrl: 'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
  catalogueUrl: 'https://edastro.com/mapcharts/files.html',
  rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
  usageBasis:
    'Purpose-published automation-ready CSV; non-commercial use accepted by the ED-Finder owner',
  attribution: 'EDAstro / CMDR Orvidius',
  sourceLastModified: 'Sun, 13 Sep 2026 15:00:58 GMT',
  sourceByteCount: 123,
  sourceSha256: 'a'.repeat(64),
  count: 1,
  nebulae: [
    {
      id: 'MAPCHARTS:42',
      source: 'EDAstro Mapcharts',
      sourceId: '42',
      name: 'Example Nebula',
      systemName: 'Example Sector AA-A h1',
      regionId: 18,
      kind: 'nebula',
      coordinates: [123.5, -44, 9001],
      poiUrl: null,
    },
  ],
} as const;

describe('galaxy nebulae', () => {
  it('preserves exact catalogue coordinates as authoritative truth', () => {
    const parsed = parseGalaxyNebulaeAsset(asset);
    expect(parsed.nebulae[0]?.positionLy).toMatchObject({
      value: { x: 123.5, y: -44, z: 9001 },
      representation: 'AUTHORITATIVE',
    });
    expect(parsed.nebulae[0]?.positionLy.provenance?.[0]?.source).toContain(
      'EDAstro',
    );
  });

  it('labels the cloud presentation ambient without weakening position truth', () => {
    const parsed = parseGalaxyNebulaeAsset(asset);
    const contribution = createGalaxyNebulaeContribution(parsed, 7);
    expect(contribution.owner).toBe('CATALOGUE');
    expect(contribution.layers[0]).toMatchObject({
      id: 'galaxy-nebulae',
      representation: 'AMBIENT',
      targetCount: 1,
      truncated: false,
    });
  });

  it('rejects duplicate source identities and attribution loss', () => {
    expect(() =>
      parseGalaxyNebulaeAsset({
        ...asset,
        count: 2,
        nebulae: [asset.nebulae[0], asset.nebulae[0]],
      }),
    ).toThrow(/Duplicate nebula identity/);
    expect(() =>
      parseGalaxyNebulaeAsset({ ...asset, rightsNotice: '' }),
    ).toThrow(/rightsNotice/);
  });

  it('accepts an explicitly empty catalogue without accepting malformed inventories', () => {
    const parsed = parseGalaxyNebulaeAsset({ ...asset, count: 0, nebulae: [] });
    expect(parsed.nebulae).toEqual([]);
    expect(createGalaxyNebulaeContribution(parsed, 1).layers[0]).toMatchObject({
      targetCount: 0,
      truncated: false,
    });
    expect(() =>
      parseGalaxyNebulaeAsset({ ...asset, count: 0, nebulae: null }),
    ).toThrow(/landmark array/);
    expect(() =>
      parseGalaxyNebulaeAsset({ ...asset, count: 1, nebulae: [] }),
    ).toThrow(/count does not match/);
    expect(() =>
      parseGalaxyNebulaeAsset({
        ...asset,
        count: 0,
        nebulae: [],
        attribution: '',
      }),
    ).toThrow(/attribution/);
  });

  it('ships the complete attributed Mapcharts nebula inventory', () => {
    const publishedAsset = JSON.parse(
      readFileSync(
        resolve(process.cwd(), 'static/assets/edastro-mapcharts-nebulae.json'),
        'utf8',
      ),
    ) as unknown;
    const parsed = parseGalaxyNebulaeAsset(publishedAsset);

    expect(parsed).toMatchObject({
      datasetId: 'edastro-mapcharts-nebulae-coordinates',
      attribution: 'EDAstro / CMDR Orvidius',
      rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
    });
    expect(parsed.sourceByteCount).toBeGreaterThan(400_000);
    expect(parsed.sourceSha256).toMatch(/^[a-f0-9]{64}$/u);
    expect(parsed.nebulae).toHaveLength(5_842);
    expect(
      parsed.nebulae.filter((nebula) => nebula.kind === 'planetary-nebula'),
    ).toHaveLength(5_496);
    expect(
      parsed.nebulae.filter((nebula) => nebula.kind === 'nebula'),
    ).toHaveLength(346);
    expect(new Set(parsed.nebulae.map((nebula) => nebula.id)).size).toBe(5_842);
  });
});
