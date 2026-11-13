// A body's surface, painted by arithmetic: no picture files and no 3D engine.
//
// Each body is a small square of pixels showing a lit ball. What is on the
// ball is made up from the body's class and a number that is the body's own
// (its id), so a body always looks the same and two bodies of one class do
// not look alike. Nothing here is a picture of the real surface: the game's
// own pictures are Frontier's, and we have no data on what a surface looks
// like. The class decides the kind of pattern and the colours.
//
//   rock, ice, metal    blotches of lighter and darker ground, and craters
//   worlds with seas    sea, land and cloud; ice at the poles
//   gas giants          bands that waver, and a storm or two
//   stars               bright grain, darker toward the edge; not shaded
//
// Pure arithmetic on numbers, so it runs and is tested without a browser.
import { isBanded } from './body-look.mjs';

/** A whole number from a text, the same on every run. */
export function seedOf(text) {
  let seed = 2166136261;
  for (const letter of String(text)) seed = Math.imul(seed ^ letter.codePointAt(0), 16777619) >>> 0;
  return seed;
}

function hash(x, y, z, seed) {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(z, 2147483647) ^ seed;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

const ease = (t) => t * t * (3 - 2 * t);
const mix = (a, b, t) => a + (b - a) * t;
const clamp01 = (v) => Math.min(1, Math.max(0, v));

/** Smooth noise in space, 0 to 1. */
function noise(x, y, z, seed) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const u = ease(x - xi);
  const v = ease(y - yi);
  const w = ease(z - zi);
  const at = (dx, dy, dz) => hash(xi + dx, yi + dy, zi + dz, seed);
  return mix(
    mix(mix(at(0, 0, 0), at(1, 0, 0), u), mix(at(0, 1, 0), at(1, 1, 0), u), v),
    mix(mix(at(0, 0, 1), at(1, 0, 1), u), mix(at(0, 1, 1), at(1, 1, 1), u), v),
    w,
  );
}

/** Noise with detail: several sizes of it laid over each other, 0 to 1. */
function cloudy(x, y, z, seed, layers = 4) {
  let total = 0;
  let size = 0.5;
  let reach = 0;
  for (let layer = 0; layer < layers; layer += 1) {
    total += noise(x, y, z, seed + layer * 1013) * size;
    reach += size;
    x *= 2.03; y *= 2.03; z *= 2.03;
    size *= 0.5;
  }
  return total / reach;
}

const shade = (colour, by) => colour.map((part) => clamp01(part * by));
const blend = (one, other, t) => one.map((part, index) => mix(part, other[index], t));

/** Classes with open water or its like: sea, land and cloud. */
const SEAS = new Set(['earth-like', 'water-world', 'ammonia-world']);

/** The kind of pattern a class gets: 'bands', 'seas' or bare 'ground'. */
export function patternOf(family) {
  if (isBanded(family)) return 'bands';
  return SEAS.has(family) ? 'seas' : 'ground';
}

/** Where the light comes from: up and to the left, a little toward the viewer. */
const LIGHT = (() => {
  const raw = [-0.52, -0.5, 0.69];
  const length = Math.hypot(...raw);
  return raw.map((part) => part / length);
})();

function ground(px, py, pz, seed, colour) {
  const lumps = cloudy(px * 2.2, py * 2.2, pz * 2.2, seed);
  const grain = cloudy(px * 9, py * 9, pz * 9, seed + 77, 3);
  let tone = 0.82 + (lumps - 0.5) * 0.9 + (grain - 0.5) * 0.25;
  // Craters: round dark spots with a pale rim, where a coarser noise peaks.
  const pits = noise(px * 5.5, py * 5.5, pz * 5.5, seed + 191);
  if (pits > 0.74) tone *= 0.72 + (pits - 0.74) * 2.2;
  return shade(colour, tone);
}

function seas(px, py, pz, seed, colour, family) {
  const land = cloudy(px * 1.9, py * 1.9, pz * 1.9, seed) + (cloudy(px * 6, py * 6, pz * 6, seed + 5, 3) - 0.5) * 0.18;
  const shore = family === 'water-world' ? 0.68 : family === 'ammonia-world' ? 0.5 : 0.53;
  const sea = shade(colour, 0.8);
  const dry = family === 'ammonia-world' ? [0.62, 0.52, 0.36] : blend([0.2, 0.42, 0.2], [0.5, 0.42, 0.28], clamp01((land - shore) * 6));
  let out = land > shore ? dry : blend(shade(sea, 0.8), sea, clamp01((land - shore + 0.25) * 4));
  // Ice toward the poles, with a ragged edge.
  const pole = Math.abs(py) + (cloudy(px * 5, py * 5, pz * 5, seed + 31, 2) - 0.5) * 0.18;
  if (pole > 0.86) out = blend(out, [0.93, 0.96, 0.98], clamp01((pole - 0.86) * 12));
  const cloud = cloudy(px * 3.1 + 7, py * 5.2, pz * 3.1, seed + 911);
  return blend(out, [0.97, 0.97, 0.97], clamp01((cloud - 0.56) * 4.5) * 0.85);
}

function bands(px, py, pz, seed, colour) {
  // Latitude, pushed about by slow noise so the bands waver and curl.
  const sway = (cloudy(px * 2.4, py * 2.4, pz * 2.4, seed) - 0.5) * 0.22;
  const curl = (cloudy(px * 7, py * 9, pz * 7, seed + 3, 3) - 0.5) * 0.06;
  const latitude = py + sway * (1 - py * py) + curl;
  const count = 5 + (seed % 5);
  const band = noise(latitude * count, 0.5, 0.5, seed + 17);
  const fine = noise(latitude * count * 3.1, 1.5, 0.5, seed + 23);
  let out = blend(shade(colour, 0.62), shade(blend(colour, [1, 1, 1], 0.35), 1.08), clamp01(band * 0.75 + fine * 0.25));
  // A storm: an oval, a little off the equator.
  const stormY = ((seed >>> 8) % 100) / 100 * 0.7 - 0.35;
  const stormAngle = ((seed >>> 16) % 628) / 100;
  const sx = Math.cos(stormAngle);
  const sz = Math.sin(stormAngle);
  const away = Math.hypot((px - sx) * 0.9, (py - stormY) * 2.4, (pz - sz) * 0.9);
  if (away < 0.42) out = blend(out, shade(blend(colour, [1, 0.55, 0.35], 0.55), 0.95), clamp01((0.42 - away) * 5) * 0.8);
  return out;
}

function starLight(px, py, pz, seed, colour, halo, facing) {
  const grain = cloudy(px * 7, py * 7, pz * 7, seed, 3);
  const cells = cloudy(px * 2.5, py * 2.5, pz * 2.5, seed + 9, 3);
  const hot = blend(colour, [1, 1, 1], 0.55);
  let out = blend(colour, hot, clamp01(0.35 + (grain - 0.5) * 1.5 + (cells - 0.5) * 0.9));
  // Darker and redder toward the edge, as a real star looks.
  return blend(halo, out, clamp01(0.35 + 0.65 * facing ** 0.55));
}

/**
 * The pixels of one body: `size` across, four numbers a pixel (red, green,
 * blue, and how solid), clear outside the ball.
 *   family  the body's class as body-look.mjs names it ("icy", "gas-giant-I")
 *   colour  that class's colour, parts 0 to 1
 *   star    true for a star, with `halo` as the colour of its edge
 *   dark    true for a black hole: a black ball
 */
export function surface({ seed, size, family, colour, star = false, halo = colour, dark = false }) {
  const data = new Uint8ClampedArray(size * size * 4);
  const pattern = patternOf(family);
  const airy = pattern !== 'ground';
  // A fixed lean, so bands and poles are not dead level.
  const lean = ((seed % 41) - 20) / 100;
  const cosLean = Math.cos(lean);
  const sinLean = Math.sin(lean);
  for (let row = 0; row < size; row += 1) {
    for (let column = 0; column < size; column += 1) {
      const x = (column + 0.5) / size * 2 - 1;
      const y = (row + 0.5) / size * 2 - 1;
      const reach = x * x + y * y;
      const index = (row * size + column) * 4;
      if (reach >= 1) continue;
      const z = Math.sqrt(1 - reach);
      // The same point on the ball, with the ball leant over.
      const px = x * cosLean - y * sinLean;
      const py = x * sinLean + y * cosLean;
      let out;
      if (dark) out = [0, 0, 0];
      else if (star) out = starLight(px, py, z, seed, colour, halo, z);
      else {
        const surfaceColour = pattern === 'bands' ? bands(px, py, z, seed, colour)
          : pattern === 'seas' ? seas(px, py, z, seed, colour, family) : ground(px, py, z, seed, colour);
        const lit = x * LIGHT[0] + y * LIGHT[1] + z * LIGHT[2];
        // Day, a soft edge to it, and a little light on the night side.
        const day = clamp01(lit * 1.25 + 0.12);
        out = shade(surfaceColour, 0.07 + 0.98 * day ** 0.85);
        // Air: a pale rim, brightest where the light skims it.
        if (airy) out = blend(out, [0.75, 0.86, 1], (1 - z) ** 2.6 * 0.55 * clamp01(lit + 0.55));
      }
      // A soft edge to the ball, one pixel wide.
      const solid = clamp01((1 - Math.sqrt(reach)) * size * 0.5);
      data[index] = out[0] * 255;
      data[index + 1] = out[1] * 255;
      data[index + 2] = out[2] * 255;
      data[index + 3] = solid * 255;
    }
  }
  return { size, data };
}

/** How many pixels across a body's picture is, from how big it is drawn. */
export function pixelsFor(drawnRadius) {
  return Math.round(Math.min(256, Math.max(40, drawnRadius * 6)) / 8) * 8;
}
