import { describe, expect, it } from 'vitest';

import {
  createNebulaCloudAtlas,
  NEBULA_CLOUD_ATLAS_PADDING,
  NEBULA_CLOUD_ATLAS_TILE_SIZE,
  NEBULA_CLOUD_ATLAS_TILES_PER_AXIS,
  type NebulaCloudAtlas,
} from './nebula-cloud-atlas';

function tileAlpha(atlas: NebulaCloudAtlas, tile: number): number[] {
  const startX = (tile % atlas.tilesPerAxis) * atlas.tileSize;
  const startY = Math.floor(tile / atlas.tilesPerAxis) * atlas.tileSize;
  return Array.from({ length: atlas.tileSize ** 2 }, (_, index) => {
    const x = startX + (index % atlas.tileSize);
    const y = startY + Math.floor(index / atlas.tileSize);
    return atlas.data[(y * atlas.width + x) * 4 + 3];
  });
}

describe('procedural nebula cloud atlas', () => {
  it('has a fixed startup budget of four 64 pixel RGBA tiles', () => {
    const atlas = createNebulaCloudAtlas();
    expect(atlas.tileSize).toBe(NEBULA_CLOUD_ATLAS_TILE_SIZE);
    expect(atlas.tilesPerAxis).toBe(NEBULA_CLOUD_ATLAS_TILES_PER_AXIS);
    expect(atlas).toMatchObject({
      width: 128,
      height: 128,
      tileSize: 64,
      tilesPerAxis: 2,
    });
    expect(atlas.data).toBeInstanceOf(Uint8Array);
    expect(atlas.data.byteLength).toBe(65_536);
    expect(atlas.data).toHaveLength(atlas.width * atlas.height * 4);
  });

  it('reproduces every pixel without sharing a mutable buffer between scenes', () => {
    const first = createNebulaCloudAtlas();
    const second = createNebulaCloudAtlas();
    expect(first.data).toEqual(second.data);
    expect(first.data).not.toBe(second.data);
  });

  it('pads each tile with transparent samples and leaves palette tinting to instances', () => {
    const atlas = createNebulaCloudAtlas();
    expect(
      atlas.data.every((channel, index) => index % 4 === 3 || channel === 255),
    ).toBe(true);
    for (let tile = 0; tile < atlas.tilesPerAxis ** 2; tile += 1) {
      const alpha = tileAlpha(atlas, tile);
      const padding = NEBULA_CLOUD_ATLAS_PADDING;
      expect(
        alpha.every((value, index) => {
          const x = index % atlas.tileSize;
          const y = Math.floor(index / atlas.tileSize);
          return (
            (x >= padding &&
              y >= padding &&
              x < atlas.tileSize - padding &&
              y < atlas.tileSize - padding) ||
            value === 0
          );
        }),
      ).toBe(true);
    }
  });

  it('has distinct irregular silhouettes with graded soft edges, not opaque discs', () => {
    const atlas = createNebulaCloudAtlas();
    const tiles = Array.from({ length: atlas.tilesPerAxis ** 2 }, (_, tile) =>
      tileAlpha(atlas, tile),
    );
    for (const [index, alpha] of tiles.entries()) {
      expect(new Set(alpha).size).toBeGreaterThan(64);
      expect(Math.max(...alpha)).toBeLessThan(255);
      expect(
        alpha[(atlas.tileSize / 2) * atlas.tileSize + atlas.tileSize / 2],
      ).toBeGreaterThan(100);
      expect(alpha).not.toEqual([...alpha].reverse());
      for (const other of tiles.slice(index + 1)) {
        expect(alpha).not.toEqual(other);
      }
      let maximumStep = 0;
      for (let y = 1; y < atlas.tileSize; y += 1) {
        for (let x = 1; x < atlas.tileSize; x += 1) {
          const pixel = y * atlas.tileSize + x;
          maximumStep = Math.max(
            maximumStep,
            Math.abs(alpha[pixel] - alpha[pixel - 1]),
            Math.abs(alpha[pixel] - alpha[pixel - atlas.tileSize]),
          );
        }
      }
      expect(maximumStep).toBeLessThan(24);
    }
  });
});
