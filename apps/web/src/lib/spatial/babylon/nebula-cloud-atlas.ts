export const NEBULA_CLOUD_ATLAS_TILE_SIZE = 64;
export const NEBULA_CLOUD_ATLAS_TILES_PER_AXIS = 2;
export const NEBULA_CLOUD_ATLAS_PADDING = 3;

export type NebulaCloudAtlas = Readonly<{
  data: Uint8Array;
  width: number;
  height: number;
  tileSize: number;
  tilesPerAxis: number;
}>;

function noiseSample(x: number, y: number, seed: number): number {
  let hash = Math.imul(x, 374_761_393) ^ Math.imul(y, 668_265_263) ^ seed;
  hash = Math.imul(hash ^ (hash >>> 13), 1_274_126_177);
  return ((hash ^ (hash >>> 16)) >>> 0) / 0xffff_ffff;
}

function interpolate(left: number, right: number, amount: number): number {
  return left + (right - left) * amount;
}

/** Smooth lattice noise stays low frequency at the fixed tile resolution. */
function smoothNoise(x: number, y: number, seed: number): number {
  const left = Math.floor(x);
  const top = Math.floor(y);
  const u = x - left;
  const v = y - top;
  const smoothU = u * u * (3 - 2 * u);
  const smoothV = v * v * (3 - 2 * v);
  return interpolate(
    interpolate(
      noiseSample(left, top, seed),
      noiseSample(left + 1, top, seed),
      smoothU,
    ),
    interpolate(
      noiseSample(left, top + 1, seed),
      noiseSample(left + 1, top + 1, seed),
      smoothU,
    ),
    smoothV,
  );
}

/**
 * Four decorative cloud silhouettes, generated once when the layer is created.
 * The fixed 128 × 128 RGBA budget is 64 KiB; caller/catalogue input cannot grow
 * it. This texture conveys no measured nebula structure or extent. White RGB
 * allows each instance to retain its palette colour; alpha supplies the shape.
 */
export function createNebulaCloudAtlas(): NebulaCloudAtlas {
  const tileSize = NEBULA_CLOUD_ATLAS_TILE_SIZE;
  const tilesPerAxis = NEBULA_CLOUD_ATLAS_TILES_PER_AXIS;
  const width = tileSize * tilesPerAxis;
  const height = width;
  const data = new Uint8Array(width * height * 4);

  for (let tile = 0; tile < tilesPerAxis * tilesPerAxis; tile += 1) {
    const tileX = (tile % tilesPerAxis) * tileSize;
    const tileY = Math.floor(tile / tilesPerAxis) * tileSize;
    const seed = Math.imul(tile + 1, 0x45d9f3b);
    for (let y = 0; y < tileSize; y += 1) {
      for (let x = 0; x < tileSize; x += 1) {
        const offset = ((tileY + y) * width + tileX + x) * 4;
        data[offset] = 255;
        data[offset + 1] = 255;
        data[offset + 2] = 255;
        // Keep adjacent atlas tiles isolated under bilinear texture filtering.
        if (
          x < NEBULA_CLOUD_ATLAS_PADDING ||
          y < NEBULA_CLOUD_ATLAS_PADDING ||
          x >= tileSize - NEBULA_CLOUD_ATLAS_PADDING ||
          y >= tileSize - NEBULA_CLOUD_ATLAS_PADDING
        ) {
          continue;
        }

        const u = (2 * x) / (tileSize - 1) - 1;
        const v = (2 * y) / (tileSize - 1) - 1;
        const broadNoise = smoothNoise(u * 1.6 + 4.3, v * 1.6 + 7.1, seed);
        const detailNoise = smoothNoise(
          u * 3.7 + 11.4,
          v * 3.7 + 3.8,
          seed ^ 0x6d2b79f5,
        );
        const support = 0.72 + broadNoise * 0.22;
        const radial = Math.max(0, 1 - (u * u + v * v) / support ** 2);
        const wisps = 0.38 + broadNoise * 0.42 + detailNoise * 0.2;
        // Squared falloff reaches zero smoothly before the padded tile edge.
        data[offset + 3] = Math.round(255 * radial ** 2 * wisps);
      }
    }
  }
  return { data, width, height, tileSize, tilesPerAxis };
}
