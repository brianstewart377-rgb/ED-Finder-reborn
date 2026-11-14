// What the Cosmos library is asked to draw for a body.
//
// Cosmos (@eluvade/cosmos, MIT) draws planets, stars and black holes with
// WebGL from a kind and a number; its planet shaders are ported from
// Deep-Fold's Pixel Planets (MIT). This file only decides, from what we know
// of a body, which kind to ask for and in which colours. It draws nothing
// and needs no browser, so it is tested as plain arithmetic.
//
// The kind comes from the body's class. A rocky or metal body hot enough for
// its rock to melt is drawn molten. Colours stay ours where our own map
// gives a class its colour (stars, gas giants, bare rock and metal); for
// worlds with seas and for ice, Cosmos's own colours are kept.
//
// A gas giant is asked for as Cosmos's banded kind, which comes with a ring
// of its own. That ring is made fully clear: the rings on our map are the
// ones in our data, drawn by draw.mjs in the colour of their kind.
import { isBanded } from './body-look.mjs';

/** The kinds Cosmos can draw, by its own names. */
export const KINDS = {
  'earth-like': 'terrain_wet',
  'water-world': 'aquatic',
  'ammonia-world': 'terrain_dry',
  icy: 'ice',
  'rocky-ice': 'barren',
  rocky: 'barren',
  'high-metal-content': 'barren',
  'metal-rich': 'barren',
  unknown: 'barren',
};

/** From this surface temperature (kelvin) bare rock and metal are drawn molten. */
export const MOLTEN_FROM_K = 1300;

/**
 * How wide the picture is drawn, in body radii. A planet fills its picture.
 * A star's picture holds its glow around it, a black hole's its disc, and a
 * banded gas giant's the room for a ring, so those are drawn wider for the
 * body itself to come out at its size.
 */
export const WIDTHS = { planet: 2, gas_giant: 4, star: 3.5, black_hole: 5 };

const WHITE = [1, 1, 1];
const mix = (one, other, t) => one.map((part, index) => part + (other[index] - part) * t);
const shade = (colour, by) => colour.map((part) => Math.min(1, part * by));
const solid = (colour) => [...colour.map((part) => Math.min(1, Math.max(0, part))), 1];

/** A class colour as the set of shades one kind of picture uses, lightest first. */
export function shadesFor(kind, colour, halo = colour) {
  const named = (names, shades) => Object.fromEntries(names.map((name, index) => [name, solid(shades[index])]));
  if (kind === 'gas_giant_ringed') {
    const clear = [0, 0, 0, 0];
    return {
      ...named(
        ['gas1', 'gas2', 'gas3', 'gas4', 'gas_dark1', 'gas_dark2', 'gas_dark3', 'gas_dark4'],
        [mix(colour, WHITE, 0.5), mix(colour, WHITE, 0.25), colour, shade(colour, 0.85),
          shade(colour, 0.72), shade(colour, 0.6), shade(colour, 0.48), shade(colour, 0.36)]),
      ring1: clear, ring2: clear, ring3: clear, ring4: clear,
    };
  }
  if (kind === 'barren') {
    return named(['base1', 'base2', 'base3', 'crater1', 'crater2'],
      [mix(colour, WHITE, 0.22), shade(colour, 0.82), shade(colour, 0.56), shade(colour, 0.5), shade(colour, 0.34)]);
  }
  if (kind === 'terrain_dry') {
    return named(['col1', 'col2', 'col3', 'col4'],
      [mix(colour, WHITE, 0.32), colour, shade(colour, 0.68), shade(colour, 0.42)]);
  }
  if (kind === 'star') {
    return named(['star1', 'star2', 'star3', 'star4', 'glow'],
      [mix(colour, WHITE, 0.8), mix(colour, WHITE, 0.4), mix(colour, halo, 0.5), shade(halo, 0.8), halo]);
  }
  return null;
}

/**
 * What to ask Cosmos for: its kind of picture, our colours for it (or none,
 * to keep its own), and how wide to draw the result in body radii.
 *   family    the body's class as body-look.mjs names it
 *   surfaceK  its surface temperature in kelvin, or null where not known
 */
export function cosmosAsk({ family, star = false, dark = false, colour, halo = null, surfaceK = null }) {
  if (star && dark) return { kind: 'black_hole', colours: null, width: WIDTHS.black_hole, whole: true };
  if (star) return { kind: 'star', colours: shadesFor('star', colour, halo ?? colour), width: WIDTHS.star, whole: true };
  if (isBanded(family)) {
    return { kind: 'gas_giant_ringed', colours: shadesFor('gas_giant_ringed', colour), width: WIDTHS.gas_giant, whole: false };
  }
  let kind = KINDS[family] ?? 'barren';
  if (kind === 'barren' && surfaceK !== null && surfaceK >= MOLTEN_FROM_K) kind = 'molten';
  return { kind, colours: shadesFor(kind, colour), width: WIDTHS.planet, whole: false };
}

/**
 * How many pixels across Cosmos is asked for, from how big the body is drawn
 * and how wide its picture is. Four times what the screen needs, so that
 * zooming in stays sharp, within bounds that keep a whole system quick.
 */
export function cosmosPixels(drawnRadius, width) {
  return Math.min(512, Math.max(128, Math.round(drawnRadius * width * 4 / 16) * 16));
}
