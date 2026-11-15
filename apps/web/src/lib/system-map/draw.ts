/**
 * The shapes of the System Map: each body shaded as a lit ball, gas giants
 * carrying bands, rings passing behind and in front of their planet, and each
 * station in orbit a small marker hanging from its body. SystemMap.svelte
 * draws them as SVG; everything here is arithmetic, tested without a browser.
 *
 * Each ring is its own band, coloured by its kind (icy, rocky, metal rich,
 * metallic). Colour never says it alone: the kind is also written under the
 * planet's name, and spoken.
 *
 * The colours are illustrative, by class, exactly as on the app's other maps:
 * no surface picture or observed weather is implied.
 *
 * Ported from the System Map trial page (`draw.mjs` in
 * `scripts/almanac-trial`, removed once it was in the site: git history has
 * it).
 */
import { stellarPresentation } from '$lib/spatial/stellar-presentation';
import type { BeltItem, BodyItem } from './layout';
import {
  familyColour,
  familyKey,
  isBanded,
  type FamilyKey,
  type Rgb,
} from './look';
import {
  ringKind,
  type MapNode,
  type PlacedStation,
  type StationMark,
} from './tree';

/** The colour of each kind of ring and belt, chosen to be told apart at a glance (parts 0 to 255). */
export const RING_COLOURS: Readonly<Record<string, Rgb>> = {
  icy: [170, 222, 250],
  rocky: [170, 122, 84],
  metal_rich: [230, 186, 80],
  metallic: [156, 162, 180],
};
export const RING_COLOUR_UNKNOWN: Rgb = [120, 120, 126];
/** How far the rings lean, in degrees. */
export const RING_TILT = -12;
/** How flat a ring looks, seen from a little above: its height over its width. */
const RING_FLAT = 0.24;
/** Where a planet's rings are drawn, in planet radii from its centre, and the gap between two. */
export const RING_FROM = 1.3;
export const RING_TO = 2.1;
const RING_GAP = 0.06;

/** A number cut to two decimals, so the picture's text stays short. */
export function n(value: number): number {
  return Number(value.toFixed(2));
}

/** A colour whose parts run from 0 to 255. */
export function rgb255(parts: readonly number[]): string {
  return `rgb(${parts.map((part) => Math.round(Math.min(255, Math.max(0, part)))).join(',')})`;
}

/** A colour whose parts run from 0 to 1, as the app's colour rules give them. */
export function rgb1(parts: readonly number[]): string {
  return rgb255(parts.map((part) => part * 255));
}

/** A whole number from a text, the same on every run. */
export function seedOf(text: string): number {
  let seed = 2166136261;
  for (const letter of String(text))
    seed = Math.imul(seed ^ (letter.codePointAt(0) as number), 16777619) >>> 0;
  return seed;
}

/** A small source of numbers from 0 to 1 that is the same on every run, from a seed. */
export function numbersFrom(seedText: string): () => number {
  let seed = seedOf(seedText);
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

export type Look = Readonly<{
  star: boolean;
  /** A black hole: a dark ball with a bright edge. */
  dark: boolean;
  family: FamilyKey;
  colour: Rgb;
  halo: Rgb | null;
  banded: boolean;
  /** What the body is, in words. */
  words: string;
}>;

/** How a body is coloured and named for the picture, from its class alone. */
export function lookOf(
  node: Pick<MapNode, 'type' | 'bodyClass' | 'spectral' | 'luminosity'>,
): Look {
  if (node.type === 'star') {
    const subtype = node.spectral
      ? `${node.spectral}${node.luminosity ? ` ${node.luminosity}` : ''}`
      : null;
    const look = stellarPresentation({
      type: node.bodyClass ?? node.spectral,
      subtype,
    });
    return {
      star: true,
      dark: look.darkCore,
      family: 'unknown',
      colour: look.coreRgb,
      halo: look.haloRgb,
      banded: false,
      words: node.bodyClass ?? look.label,
    };
  }
  const family = familyKey(node.bodyClass);
  return {
    star: false,
    dark: false,
    family,
    colour: familyColour(family),
    halo: null,
    banded: isBanded(family),
    words:
      node.bodyClass ??
      (node.type === 'barycentre' ? 'Barycentre' : 'Class not known'),
  };
}

/** What a screen reader and a tooltip say of a body. */
export function spokenOf(node: MapNode): string {
  const parts = [`${node.short}, ${lookOf(node).words}`];
  const slots = node.groundSlots?.count ?? null;
  if (node.orbit.length > 0) parts.push(`${node.orbit.length} in orbit`);
  if (node.ground.length > 0) parts.push(`${node.ground.length} on the ground`);
  if (slots !== null)
    parts.push(`${slots} ground ${slots === 1 ? 'slot' : 'slots'} predicted`);
  if (node.ringWords) parts.push(node.ringWords);
  if (node.guessedParent === 'name') parts.push('placed by its name');
  else if (node.guessedParent === 'distance')
    parts.push('placed by its distance');
  else if (node.placedBy === 'name')
    parts.push('in a pair placed by its names');
  else if (node.placedBy === 'distance')
    parts.push('in a pair placed by its distance');
  return parts.join('; ');
}

/** What a tooltip says of a station. */
export function spokenOfStation(station: PlacedStation): string {
  return `${station.name}, ${station.kind}${station.guess ? ', placed by its distance: our data does not say which body' : ''}`;
}

export type RingBand = Readonly<{
  from: number;
  to: number;
  colour: Rgb;
  kind: string | null;
}>;

/** Each of a planet's rings as a band, from the inside out: where it runs, in planet radii, and its colour. */
export function ringBandsOf(node: Pick<MapNode, 'rings'>): RingBand[] {
  const count = node.rings.length;
  if (count === 0) return [];
  const each = (RING_TO - RING_FROM - RING_GAP * (count - 1)) / count;
  return node.rings.map((ring, index) => {
    const from = RING_FROM + index * (each + RING_GAP);
    return {
      from,
      to: from + each,
      colour:
        (ring.ringClass && RING_COLOURS[ring.ringClass]) || RING_COLOUR_UNKNOWN,
      kind: ringKind(ring),
    };
  });
}

/**
 * Half of a flat band between two distances from the centre: the half that
 * passes in front of the planet, or the half that passes behind. Drawn as a
 * filled shape, so the band is thin where it turns away and wide at the sides.
 */
export function halfBand(from: number, to: number, front: boolean): string {
  const [outer, inner] = front ? [0, 1] : [1, 0];
  return (
    `M ${n(-to)} 0 A ${n(to)} ${n(to * RING_FLAT)} 0 0 ${outer} ${n(to)} 0 ` +
    `L ${n(from)} 0 A ${n(from)} ${n(from * RING_FLAT)} 0 0 ${inner} ${n(-from)} 0 Z`
  );
}

export type Shape = Readonly<{ d: string; fill: string; opacity?: number }>;

/**
 * One half of a planet's rings, as filled shapes: each ring one band of its
 * kind's colour, and within it a few lanes of lighter and darker dust, so it
 * reads as a ring of grains and not a strip of card. The lanes are for the
 * eye only: one ring in our data, one band.
 */
export function ringShapes(
  item: Pick<BodyItem, 'node' | 'r'>,
  front: boolean,
): Shape[] {
  const { node, r } = item;
  if (node.rings.length === 0) return [];
  const next = numbersFrom(`ring-${node.id}`);
  return ringBandsOf(node).flatMap((band) => {
    const shapes: Shape[] = [
      {
        d: halfBand(band.from * r, band.to * r, front),
        fill: rgb255(band.colour),
      },
    ];
    const width = band.to - band.from;
    for (let lane = 0; lane < 5; lane += 1) {
      const from = band.from + (width * (lane + 0.15 + next() * 0.3)) / 5;
      const to = Math.min(band.to, from + width * (0.04 + next() * 0.1));
      shapes.push({
        d: halfBand(from * r, to * r, front),
        fill: next() > 0.45 ? '#000' : '#fff',
        opacity: n(0.1 + next() * 0.16),
      });
    }
    return shapes;
  });
}

/** The shadow the rings throw across their planet, just above where they pass in front. */
export function ringShadow(item: Pick<BodyItem, 'node' | 'r'>): string | null {
  if (item.node.rings.length === 0) return null;
  return halfBand(RING_FROM * item.r, RING_TO * item.r, true);
}

export type Stripe = Readonly<{ y: number; height: number; light: boolean }>;

/** The bands across a gas giant drawn as a plain ball, top to bottom. */
export function stripesOf(item: Pick<BodyItem, 'node' | 'r'>): Stripe[] {
  const { node, r } = item;
  const next = numbersFrom(node.id);
  let y = -r;
  let light = next() > 0.5;
  const stripes: Stripe[] = [];
  while (y < r) {
    const height = r * (0.12 + next() * 0.2);
    stripes.push({ y: n(y), height: n(height), light });
    y += height;
    light = !light;
  }
  return stripes;
}

/** The colours across a star drawn as a plain ball: a hot core, its colour, then its edge. */
export function starStops(look: Look): [string, string, string] {
  const core = look.colour.map((part) => part + (1 - part) * 0.55);
  return [rgb1(core), rgb1(look.colour), rgb1(look.halo ?? look.colour)];
}

export type Rock = Readonly<{
  cx: number;
  cy: number;
  r: number;
  opacity: number;
}>;

/** The rocks of an asteroid belt, scattered in its oval. The same for the same belt every time. */
export function rocksOf(item: Pick<BeltItem, 'w' | 'h' | 'belt'>): Rock[] {
  const next = numbersFrom(`belt-${item.belt.body}-${item.belt.name ?? ''}`);
  const rocks: Rock[] = [];
  for (let count = 0; count < 30; count += 1) {
    const angle = next() * Math.PI * 2;
    const reach = Math.sqrt(next());
    rocks.push({
      cx: n(Math.cos(angle) * reach * (item.w / 2)),
      cy: n(Math.sin(angle) * reach * (item.h / 2)),
      r: n(0.6 + next() * 1.5),
      opacity: n(0.45 + next() * 0.5),
    });
  }
  return rocks;
}

/** A belt's colour, by its kind. */
export function beltColour(item: Pick<BeltItem, 'belt'>): string {
  return rgb255(
    (item.belt.ringClass && RING_COLOURS[item.belt.ringClass]) ||
      RING_COLOUR_UNKNOWN,
  );
}

/** What a tooltip says of a belt. */
export function spokenOfBelt(item: Pick<BeltItem, 'belt'>): string {
  const kind = ringKind(item.belt);
  return `${item.belt.name ?? 'Asteroid belt'}, ${kind ? `${kind} belt` : 'belt, kind not known'}`;
}

/** The mark inside each kind of station marker: a filled diamond, an open one, a bar or a cross. */
export const STATION_GLYPHS: Readonly<
  Record<StationMark, Readonly<{ d: string; filled: boolean; heavy?: boolean }>>
> = {
  starport: { d: 'M0 -3.2 L3.2 0 L0 3.2 L-3.2 0 Z', filled: true },
  outpost: { d: 'M0 -3.2 L3.2 0 L0 3.2 L-3.2 0 Z', filled: false },
  megaship: { d: 'M-3.4 0 H3.4', filled: false, heavy: true },
  building: { d: 'M0 -3.2 V3.2 M-3.2 0 H3.2', filled: false },
};
