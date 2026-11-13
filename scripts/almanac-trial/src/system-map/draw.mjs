// Draws a laid-out system as an SVG picture: plain shapes the browser draws
// itself, with no 3D engine. Each body is shaded as a lit ball, gas giants
// carry bands, rings pass behind and in front of their planet, and each
// station in orbit is a small marker hanging from its body.
//
// Each ring is its own band, coloured by its kind (icy, rocky, metal rich,
// metallic). Colour never says it alone: the kind is also written under the
// planet's name, and spoken.
//
// The colours are illustrative, by class, exactly as on our own map: no
// surface picture or observed weather is implied.
//
// Everything here returns text, so it can be tested without a browser. Every
// name that comes from data is escaped before it goes into the picture.
import { stellarPresentation } from '../../../../apps/web/src/lib/spatial/stellar-presentation.ts';
import { FAMILIES, familyOf, isBanded } from './body-look.mjs';
import { ringKind } from './tree.mjs';

/** The colour of each kind of ring and belt, chosen to be told apart at a glance. */
export const RING_COLOURS = {
  icy: [170, 222, 250],
  rocky: [170, 122, 84],
  metal_rich: [230, 186, 80],
  metallic: [156, 162, 180],
};
const RING_COLOUR_UNKNOWN = [120, 120, 126];
const RING_TILT = -12;
/** How flat a ring looks, seen from a little above: its height over its width. */
const RING_FLAT = 0.24;
/** Where a planet's rings are drawn, in planet radii from its centre, and the gap between two. */
const RING_FROM = 1.3;
const RING_TO = 2.1;
const RING_GAP = 0.06;

/** Text made safe to put inside the picture. */
export function safe(text) {
  return String(text)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

/** A colour whose parts run from 0 to 255. */
function rgb255(parts) {
  return `rgb(${parts.map((part) => Math.round(Math.min(255, Math.max(0, part)))).join(',')})`;
}

/** A colour whose parts run from 0 to 1, as our own map's rules give them. */
function rgb1(parts) {
  return rgb255(parts.map((part) => part * 255));
}

function n(value) {
  return Number(value.toFixed(2));
}

/** A small source of numbers that is the same on every run, from a seed. */
function numbersFrom(seedText) {
  let seed = 2166136261;
  for (const letter of String(seedText)) seed = Math.imul(seed ^ letter.codePointAt(0), 16777619) >>> 0;
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

/** How a body is coloured and named for the picture, from its class alone. */
export function lookOf(node) {
  if (node.type === 'star') {
    const subtype = node.spectral ? `${node.spectral}${node.luminosity ? ` ${node.luminosity}` : ''}` : null;
    const look = stellarPresentation({ type: node.bodyClass ?? node.spectral, subtype });
    return {
      star: true, dark: look.darkCore, colour: look.coreRgb, halo: look.haloRgb, banded: false,
      words: node.bodyClass ?? look.label,
    };
  }
  const family = familyOf(node.bodyClass);
  return {
    star: false, dark: false, colour: FAMILIES[family].colour, halo: null, banded: isBanded(family),
    words: node.bodyClass ?? 'Class not known',
  };
}

/** What a screen reader and a tooltip say of a body. */
export function spokenOf(node) {
  const parts = [`${node.short}, ${lookOf(node).words}`];
  const slots = node.groundSlots?.count ?? null;
  if (node.orbit.length > 0) parts.push(`${node.orbit.length} in orbit`);
  if (node.ground.length > 0) parts.push(`${node.ground.length} on the ground`);
  if (slots !== null) parts.push(`${slots} ground ${slots === 1 ? 'slot' : 'slots'} predicted`);
  if (node.ringWords) parts.push(node.ringWords);
  return parts.join('; ');
}

/** Each of a planet's rings as a band, from the inside out: where it runs, in planet radii, and its colour. */
export function ringBandsOf(node) {
  const count = node.rings.length;
  const each = (RING_TO - RING_FROM - RING_GAP * (count - 1)) / count;
  return node.rings.map((ring, index) => {
    const from = RING_FROM + index * (each + RING_GAP);
    return { from, to: from + each, colour: RING_COLOURS[ring.ringClass] ?? RING_COLOUR_UNKNOWN, kind: ringKind(ring) };
  });
}

/**
 * Half of a flat band between two distances from the centre: the half that
 * passes in front of the planet, or the half that passes behind. Drawn as a
 * filled shape, so the band is thin where it turns away and wide at the sides.
 */
function halfBand(from, to, front) {
  const [outer, inner] = front ? [0, 1] : [1, 0];
  return `M ${n(-to)} 0 A ${n(to)} ${n(to * RING_FLAT)} 0 0 ${outer} ${n(to)} 0 `
    + `L ${n(from)} 0 A ${n(from)} ${n(from * RING_FLAT)} 0 0 ${inner} ${n(-from)} 0 Z`;
}

function ringOf(item, front) {
  const { node, r } = item;
  if (node.rings.length === 0) return '';
  // Each ring is one band of its kind's colour. Within it, a few lanes of
  // lighter and darker dust, so it reads as a ring of grains and not a strip
  // of card. The lanes are for the eye only: one ring in our data, one band.
  const next = numbersFrom(`ring-${node.id}`);
  const bands = ringBandsOf(node)
    .map((band) => {
      let lanes = `<path d="${halfBand(band.from * r, band.to * r, front)}" fill="${rgb255(band.colour)}"/>`;
      const width = band.to - band.from;
      for (let lane = 0; lane < 5; lane += 1) {
        const from = band.from + width * (lane + 0.15 + next() * 0.3) / 5;
        const to = Math.min(band.to, from + width * (0.04 + next() * 0.1));
        lanes += `<path d="${halfBand(from * r, to * r, front)}" fill="${next() > 0.45 ? '#000' : '#fff'}" opacity="${n(0.1 + next() * 0.16)}"/>`;
      }
      return lanes;
    })
    .join('');
  return `<g class="sm-ring" transform="rotate(${RING_TILT})" opacity="${front ? 0.92 : 0.5}">${bands}</g>`;
}

/** The shadow the rings throw across their planet, just above where they pass in front. */
function ringShadowOf(item) {
  const { node, r } = item;
  if (node.rings.length === 0) return '';
  const id = `sm-ringclip-${safe(node.id)}`;
  return `<clipPath id="${id}"><circle r="${n(r)}"/></clipPath><g clip-path="url(#${id})">`
    + `<g transform="rotate(${RING_TILT}) translate(0 ${n(-0.14 * r)})" fill="#000" opacity="0.34">`
    + `<path d="${halfBand(RING_FROM * r, RING_TO * r, true)}"/></g></g>`;
}

function bandsOf(item) {
  const { node, r } = item;
  const next = numbersFrom(node.id);
  let y = -r;
  let marks = '';
  let light = next() > 0.5;
  while (y < r) {
    const height = r * (0.12 + next() * 0.2);
    marks += `<rect x="${n(-r)}" y="${n(y)}" width="${n(2 * r)}" height="${n(height)}" `
      + `fill="${light ? '#fff' : '#000'}" opacity="${light ? 0.1 : 0.16}"/>`;
    y += height;
    light = !light;
  }
  return `<clipPath id="sm-clip-${safe(node.id)}"><circle r="${n(r)}"/></clipPath>`
    + `<g clip-path="url(#sm-clip-${safe(node.id)})">${marks}</g>`;
}

function bodyOf(item, pictureFor) {
  const { node, r } = item;
  const look = lookOf(node);
  // A painted surface, where the page can make one (surface.mjs); else the
  // plain shaded ball below, which needs nothing but the browser's shapes.
  const painted = pictureFor ? pictureFor(item, look) : null;
  let shape;
  if (painted) {
    const glow = look.star
      ? `<circle r="${n(r * 1.3)}" fill="${rgb1(look.halo)}" opacity="${look.dark ? 0.35 : 0.55}" filter="url(#sm-glow)"/>`
      : '';
    const edge = look.dark ? `<circle r="${n(r)}" fill="none" stroke="${rgb1(look.halo)}" stroke-width="${n(Math.max(1.5, r * 0.08))}"/>` : '';
    shape = `${glow}<image class="sm-surface" href="${safe(painted)}" x="${n(-r)}" y="${n(-r)}" width="${n(2 * r)}" height="${n(2 * r)}"/>`
      + `${edge}${look.star ? '' : ringShadowOf(item)}`;
  } else if (look.star && look.dark) {
    shape = `<circle r="${n(r * 1.25)}" fill="${rgb1(look.halo)}" opacity="0.35" filter="url(#sm-glow)"/>`
      + `<circle r="${n(r)}" fill="#000" stroke="${rgb1(look.halo)}" stroke-width="${n(Math.max(1.5, r * 0.08))}"/>`;
  } else if (look.star) {
    shape = `<circle r="${n(r * 1.3)}" fill="${rgb1(look.halo)}" opacity="0.55" filter="url(#sm-glow)"/>`
      + `<circle r="${n(r)}" fill="url(#sm-star-${safe(node.id)})"/>`;
  } else {
    shape = `<circle r="${n(r)}" fill="${rgb1(look.colour)}"/>${look.banded ? bandsOf(item) : ''}`
      + `${ringShadowOf(item)}<circle r="${n(r)}" fill="url(#sm-shade)"/>`;
  }
  // Two numbers side by side, never one over the other: sites in our data
  // (a triangle) and ground slots the site predicts (an empty square). They
  // do not count the same thing, so the page does not make a fraction of them.
  const { mark } = item;
  const ground = !mark
    ? ''
    : `<text class="sm-ground" x="${n(mark.x)}" y="${n(mark.y)}">`
      + `${mark.built === null ? '' : `<tspan>&#9650;${mark.built}</tspan>`}`
      + `${mark.slots === null ? '' : `<tspan class="sm-slots"${mark.built === null ? '' : ' dx="3"'}>&#9633;${mark.slots}</tspan>`}</text>`;
  const spoken = safe(spokenOf(node));
  return `<g class="sm-body${node.guessedParent ? ' sm-guessed' : ''}" data-id="${safe(node.id)}" tabindex="0" role="button" `
    + `aria-label="${spoken}" transform="translate(${n(item.x)} ${n(item.y)})"><title>${spoken}</title>`
    + `${ringOf(item, false)}${shape}${ringOf(item, true)}${ground}`
    + `<circle class="sm-hit" r="${n(Math.max(r, 7) + 3)}" fill="transparent"/></g>`;
}

function starGradients(layout) {
  return layout.items
    .filter((item) => item.kind === 'body' && item.node.type === 'star' && !lookOf(item.node).dark)
    .map((item) => {
      const look = lookOf(item.node);
      const core = look.colour.map((part) => part + (1 - part) * 0.55);
      return `<radialGradient id="sm-star-${safe(item.node.id)}" cx="0.42" cy="0.4" r="0.62">`
        + `<stop offset="0" stop-color="${rgb1(core)}"/><stop offset="0.55" stop-color="${rgb1(look.colour)}"/>`
        + `<stop offset="1" stop-color="${rgb1(look.halo)}"/></radialGradient>`;
    })
    .join('');
}

function beltOf(item, index) {
  const next = numbersFrom(`belt-${index}`);
  const colour = rgb255(RING_COLOURS[item.belt.ringClass] ?? RING_COLOUR_UNKNOWN);
  let rocks = '';
  for (let count = 0; count < 30; count += 1) {
    const angle = next() * Math.PI * 2;
    const reach = Math.sqrt(next());
    const x = Math.cos(angle) * reach * (item.w / 2);
    const y = Math.sin(angle) * reach * (item.h / 2);
    rocks += `<circle cx="${n(x)}" cy="${n(y)}" r="${n(0.6 + next() * 1.5)}" opacity="${n(0.45 + next() * 0.5)}"/>`;
  }
  const kind = ringKind(item.belt);
  const name = safe(`${item.belt.name ?? 'Asteroid belt'}, ${kind ? `${kind} belt` : 'belt, kind not known'}`);
  return `<g class="sm-belt" fill="${colour}" transform="translate(${n(item.x)} ${n(item.y)})"><title>${name}</title>${rocks}</g>`;
}

const STATION_GLYPHS = {
  starport: '<path d="M0 -3.2 L3.2 0 L0 3.2 L-3.2 0 Z" fill="currentColor" stroke="none"/>',
  outpost: '<path d="M0 -3.2 L3.2 0 L0 3.2 L-3.2 0 Z" fill="none"/>',
  megaship: '<path d="M-3.4 0 H3.4" stroke-width="2.2" fill="none"/>',
  building: '<path d="M0 -3.2 V3.2 M-3.2 0 H3.2" fill="none"/>',
};

/** What a screen reader and a tooltip say of a station. */
export function spokenOfStation(station) {
  return `${station.name}, ${station.kind}${station.guess ? ', placed by its distance: our data does not say which body' : ''}`;
}

function stationOf(item) {
  const { station, size } = item;
  const half = size / 2;
  const spoken = safe(spokenOfStation(station));
  return `<g class="sm-station${station.guess ? ' sm-guess' : ''}" data-station="${safe(station.id)}" tabindex="0" role="button" `
    + `aria-label="${spoken}" transform="translate(${n(item.x)} ${n(item.y)})"><title>${spoken}</title>`
    + `<rect x="${-half}" y="${-half}" width="${size}" height="${size}" rx="2"/>${STATION_GLYPHS[station.mark] ?? ''}</g>`;
}

/**
 * The whole picture, as SVG text, with a margin around what is drawn.
 * `pictureFor(item, look)` may give the address of a painted picture for a
 * body (the page makes them with surface.mjs); without it, or where it gives
 * nothing, the body is a plain shaded ball.
 */
export function pictureOf(layout, margin = 28, pictureFor = null) {
  const { box } = layout;
  const view = [box.x0 - margin, box.y0 - margin, box.x1 - box.x0 + margin * 2, box.y1 - box.y0 + margin * 2].map(n);
  const lines = layout.lines
    .map((line) => `<line class="sm-${line.kind}" x1="${n(line.x1)}" y1="${n(line.y1)}" x2="${n(line.x2)}" y2="${n(line.y2)}"/>`)
    .join('');
  let belts = 0;
  const bodies = layout.items.filter((item) => item.kind === 'body').map((item) => bodyOf(item, pictureFor)).join('');
  const others = layout.items
    .filter((item) => item.kind !== 'body')
    .map((item) => {
      if (item.kind === 'station') return stationOf(item);
      belts += 1;
      return beltOf(item, belts);
    })
    .join('');
  const labels = layout.labels
    .map((label) => `<text class="${label.small ? 'sm-caption' : 'sm-label'}" x="${n(label.x)}" y="${n(label.y)}" text-anchor="${label.anchor}">${safe(label.text)}</text>`)
    .join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" class="sm" viewBox="${view.join(' ')}" data-box="${view.join(' ')}">`
    + '<defs>'
    // One light for every ball, from the upper left: a bright spot, then the
    // colour itself, then shadow deepening toward the far edge.
    + '<radialGradient id="sm-shade" cx="0.3" cy="0.27" r="0.86">'
    + '<stop offset="0" stop-color="#fff" stop-opacity="0.62"/><stop offset="0.24" stop-color="#fff" stop-opacity="0.06"/>'
    + '<stop offset="0.46" stop-color="#000" stop-opacity="0.04"/><stop offset="0.8" stop-color="#000" stop-opacity="0.62"/>'
    + '<stop offset="1" stop-color="#000" stop-opacity="0.92"/></radialGradient>'
    + '<filter id="sm-glow" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="5"/></filter>'
    + `${starGradients(layout)}</defs>`
    + `<g class="sm-lines">${lines}</g>${others}${bodies}<g class="sm-labels">${labels}</g></svg>`;
}
