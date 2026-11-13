// Draws a laid-out system as an SVG picture: plain shapes the browser draws
// itself, with no 3D engine. Each body is shaded as a lit ball, gas giants
// carry bands, rings pass behind and in front of their planet, and each
// station in orbit is a small marker hanging from its body.
//
// The colours are illustrative, by class, exactly as on our own map: no
// surface picture or observed weather is implied.
//
// Everything here returns text, so it can be tested without a browser. Every
// name that comes from data is escaped before it goes into the picture.
import { stellarPresentation } from '../../../../apps/web/src/lib/spatial/stellar-presentation.ts';
import { FAMILIES, familyOf, isBanded } from './body-look.mjs';

const RING_COLOURS = {
  icy: [200, 225, 240],
  rocky: [176, 150, 120],
  metal_rich: [205, 185, 140],
  metallic: [190, 190, 200],
};
const RING_TILT = -12;

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
  if (node.orbit.length > 0) parts.push(`${node.orbit.length} in orbit`);
  if (node.ground.length > 0) parts.push(`${node.ground.length} on the ground`);
  if (node.rings.length > 0) parts.push(node.rings.length === 1 ? 'ringed' : `${node.rings.length} rings`);
  return parts.join('; ');
}

function ringHalf(r, front) {
  const sweep = front ? 0 : 1;
  const wide = `M ${n(-1.85 * r)} 0 A ${n(1.85 * r)} ${n(0.44 * r)} 0 0 ${sweep} ${n(1.85 * r)} 0`;
  const thin = `M ${n(-1.42 * r)} 0 A ${n(1.42 * r)} ${n(0.33 * r)} 0 0 ${sweep} ${n(1.42 * r)} 0`;
  return { wide, thin };
}

function ringOf(item, front) {
  const { node, r } = item;
  if (node.rings.length === 0) return '';
  const colour = rgb255(RING_COLOURS[node.rings[0].ringClass] ?? [170, 170, 175]);
  const half = ringHalf(r, front);
  return `<g transform="rotate(${RING_TILT})" fill="none" stroke="${colour}" stroke-linecap="butt">`
    + `<path d="${half.wide}" stroke-width="${n(0.34 * r)}" opacity="${front ? 0.8 : 0.5}"/>`
    + `<path d="${half.thin}" stroke-width="${n(0.12 * r)}" opacity="${front ? 0.55 : 0.35}"/></g>`;
}

/** The shadow a ring throws across its planet, just above where the ring passes in front. */
function ringShadowOf(item) {
  const { node, r } = item;
  if (node.rings.length === 0) return '';
  const id = `sm-ringclip-${safe(node.id)}`;
  return `<clipPath id="${id}"><circle r="${n(r)}"/></clipPath><g clip-path="url(#${id})">`
    + `<g transform="rotate(${RING_TILT}) translate(0 ${n(-0.16 * r)})" fill="none" stroke="#000" opacity="0.34">`
    + `<path d="${ringHalf(r, true).wide}" stroke-width="${n(0.3 * r)}"/></g></g>`;
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

function bodyOf(item) {
  const { node, r } = item;
  const look = lookOf(node);
  let shape;
  if (look.star && look.dark) {
    shape = `<circle r="${n(r * 1.25)}" fill="${rgb1(look.halo)}" opacity="0.35" filter="url(#sm-glow)"/>`
      + `<circle r="${n(r)}" fill="#000" stroke="${rgb1(look.halo)}" stroke-width="${n(Math.max(1.5, r * 0.08))}"/>`;
  } else if (look.star) {
    shape = `<circle r="${n(r * 1.3)}" fill="${rgb1(look.halo)}" opacity="0.55" filter="url(#sm-glow)"/>`
      + `<circle r="${n(r)}" fill="url(#sm-star-${safe(node.id)})"/>`;
  } else {
    shape = `<circle r="${n(r)}" fill="${rgb1(look.colour)}"/>${look.banded ? bandsOf(item) : ''}`
      + `${ringShadowOf(item)}<circle r="${n(r)}" fill="url(#sm-shade)"/>`;
  }
  const ground = node.ground.length > 0
    ? `<text class="sm-ground" x="${n(r * 0.72 + 2)}" y="${n(r * 0.72 + 8)}">&#9650;${node.ground.length}</text>`
    : '';
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
  const colour = rgb255(RING_COLOURS[item.belt.ringClass] ?? [150, 140, 128]);
  let rocks = '';
  for (let count = 0; count < 30; count += 1) {
    const angle = next() * Math.PI * 2;
    const reach = Math.sqrt(next());
    const x = Math.cos(angle) * reach * (item.w / 2);
    const y = Math.sin(angle) * reach * (item.h / 2);
    rocks += `<circle cx="${n(x)}" cy="${n(y)}" r="${n(0.6 + next() * 1.5)}" opacity="${n(0.45 + next() * 0.5)}"/>`;
  }
  const name = safe(item.belt.name ?? 'Asteroid belt');
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

/** The whole picture, as SVG text, with a margin around what is drawn. */
export function pictureOf(layout, margin = 28) {
  const { box } = layout;
  const view = [box.x0 - margin, box.y0 - margin, box.x1 - box.x0 + margin * 2, box.y1 - box.y0 + margin * 2].map(n);
  const lines = layout.lines
    .map((line) => `<line class="sm-${line.kind}" x1="${n(line.x1)}" y1="${n(line.y1)}" x2="${n(line.x2)}" y2="${n(line.y2)}"/>`)
    .join('');
  let belts = 0;
  const bodies = layout.items.filter((item) => item.kind === 'body').map(bodyOf).join('');
  const others = layout.items
    .filter((item) => item.kind !== 'body')
    .map((item) => {
      if (item.kind === 'station') return stationOf(item);
      belts += 1;
      return beltOf(item, belts);
    })
    .join('');
  const labels = layout.labels
    .map((label) => `<text class="sm-label" x="${n(label.x)}" y="${n(label.y)}" text-anchor="${label.anchor}">${safe(label.text)}</text>`)
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
