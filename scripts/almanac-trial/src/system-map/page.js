// The System Map trial page. It reads the systems the data step fetched,
// draws the chosen one as a picture (tree.mjs, layout.mjs, draw.mjs), and
// handles the three things a person does: choose a system, move about the
// picture, and click a body or a station to read about it.
import { RING_KINDS, buildTree, readSystem, ringKind } from './tree.mjs';
import { layoutOf } from './layout.mjs';
import { RING_COLOURS, lookOf, pictureOf } from './draw.mjs';
import { familyOf } from './body-look.mjs';
import { pixelsFor, seedOf, surface } from './surface.mjs';

const stage = document.querySelector('#stage');
const about = document.querySelector('#about');
const chooser = document.querySelector('#system');
const problem = document.querySelector('#problem');

/** How much of a long system's width fits the screen when it opens, in the picture's own units. */
const READABLE_WIDTH = 760;
/** The most a small system is enlarged when it opens: screen pixels for each of the picture's units. */
const LARGEST_SCALE = 1.9;

let tree = null;
let view = null;
let whole = null;

function el(tag, text, className) {
  const made = document.createElement(tag);
  if (text != null) made.textContent = text;
  if (className) made.className = className;
  return made;
}

function facts(pairs) {
  const list = el('dl');
  for (const [name, value] of pairs) {
    if (value == null) continue;
    list.append(el('dt', name), el('dd', value));
  }
  return list;
}

const whole1 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const BY_DISTANCE = 'placed by its distance: our data does not say which body';

function listOf(title, stations) {
  if (stations.length === 0) return [];
  const list = el('ul');
  for (const station of stations) {
    list.append(el('li', `${station.name}, ${station.kind}${station.guess ? ` (${BY_DISTANCE})` : ''}`));
  }
  return [el('h3', title), list];
}

/** What the panel says of a body's ground slots, in plain words. Null for a star. */
function groundSlotsInWords(node) {
  if (node.type === 'star') return null;
  const { count, why, missing } = node.groundSlots;
  if (why === 'predicted') return `${count} (predicted by our site; the game is the only sure source)`;
  if (why === 'cannot land') return 'none: it cannot be landed on';
  if (why === 'facts missing') {
    return `not known: our data lacks this body's ${missing.join(', ') || 'facts'}, so the site makes no prediction`;
  }
  return 'not known';
}

/** A planet's rings or a star's belts, one to a line, from the inside out. */
function ringList(title, rings, noun) {
  if (rings.length === 0) return [];
  const list = el('ul');
  for (const ring of rings) list.append(el('li', `${ring.name ?? noun}: ${ringKind(ring) ?? 'kind not known'}`));
  return [el('h3', title), list];
}

function showSystem() {
  const all = [...tree.nodes.values()];
  const inOrbit = all.flatMap((node) => node.orbit);
  const guesses = inOrbit.filter((station) => station.guess).length;
  const onGround = all.reduce((total, node) => total + node.ground.length, 0);
  const guessedParents = all.filter((node) => node.guessedParent && node.type !== 'barycentre').length;
  const landable = all.filter((node) => node.landable);
  const predicted = landable.filter((node) => node.groundSlots.why === 'predicted').length;
  const lacking = landable.filter((node) => node.groundSlots.why === 'facts missing');
  const lacked = [...new Set(lacking.flatMap((node) => node.groundSlots.missing))].join(', ');
  about.replaceChildren(
    el('h2', tree.name),
    el('p', 'Click a body or a station to read about it.', 'kind'),
    facts([
      ['Bodies', String(all.filter((node) => node.type !== 'barycentre').length)],
      ['In orbit', `${inOrbit.length}${guesses > 0 ? `, of which ${guesses} placed by distance` : ''}`],
      ['On the ground', String(onGround)],
      ['Ground slots', `predicted for ${predicted} of the ${landable.length} bodies that can be landed on`],
    ]),
    ...(lacking.length > 0
      ? [el('p', `No prediction for ${lacking.length} of them: our data lacks their ${lacked}.`, 'note')]
      : []),
    ...(inOrbit.length + onGround === 0 ? [el('p', 'Our data holds nothing built in this system yet.', 'note')] : []),
    ...(guessedParents > 0
      ? [el('p', `${guessedParents} bodies come with no parent in our data. They are placed by their name or their distance.`, 'note')]
      : []),
    ...listOf('Not placed at any body', tree.notPlaced),
  );
}

function showBody(node) {
  const look = lookOf(node);
  const parent = [...tree.nodes.values()].find((one) => one.children.includes(node)) ?? null;
  let orbits = null;
  if (parent && parent.type === 'barycentre') {
    orbits = `a pair with ${parent.children.filter((one) => one !== node).map((one) => one.short).join(', ')}`;
  } else if (parent) {
    orbits = parent.name;
  }
  if (node.guessedParent === 'name') orbits = `${orbits} (taken from its name: our data gives no parent)`;
  if (node.guessedParent === 'distance') orbits = `${orbits} (placed by its distance: our data gives no parent)`;
  about.replaceChildren(
    el('h2', node.name),
    el('p', look.words, 'kind'),
    facts([
      ['Orbits', orbits],
      ['Radius', node.radiusKm ? `${whole1.format(node.radiusKm)} km` : null],
      ['From arrival', node.arrivalLs != null ? `${whole1.format(node.arrivalLs)} ls` : null],
      ['Star class', node.spectral ? `${node.spectral}${node.luminosity ? ` ${node.luminosity}` : ''}` : null],
      ['Can be landed on', node.type === 'star' ? null : (node.landable ? 'yes' : 'no')],
      ['Ground slots', groundSlotsInWords(node)],
    ]),
    ...ringList(node.rings.length === 1 ? 'Ring' : 'Rings, from the inside out', node.rings, 'Ring'),
    ...ringList(node.belts.length === 1 ? 'Belt' : 'Belts', node.belts, 'Belt'),
    ...listOf('In orbit', node.orbit),
    ...listOf('On the ground', node.ground),
    ...listOf('Kind not known', node.unknownKind),
    ...(node.orbit.length + node.ground.length + node.unknownKind.length === 0
      ? [el('p', 'Our data holds nothing built here.', 'note')] : []),
  );
}

function showStation(station) {
  const at = [...tree.nodes.values()].find((node) => node.orbit.includes(station));
  about.replaceChildren(
    el('h2', station.name),
    el('p', station.kind, 'kind'),
    facts([
      ['In orbit of', at ? at.name : null],
      ['From arrival', station.arrivalLs != null ? `${whole1.format(station.arrivalLs)} ls` : null],
    ]),
    ...(station.guess ? [el('p', `It is ${BY_DISTANCE}.`, 'note')] : []),
  );
}

/**
 * The view a system opens on, as the game's own map does it: bodies at a size
 * that can be read. A small system is shown whole, in the middle, and never
 * blown up past that size. A long one starts at its left end and runs off to
 * the right; dragging reaches the rest, and the button shows all of it.
 */
function opening() {
  const size = stage.getBoundingClientRect();
  const fits = Math.min(size.width / whole.w, size.height / whole.h);
  const scale = Math.min(LARGEST_SCALE, Math.max(fits, size.width / READABLE_WIDTH));
  const w = size.width / scale;
  const h = size.height / scale;
  return {
    x: whole.w <= w ? whole.x - (w - whole.w) / 2 : whole.x,
    y: whole.h <= h ? whole.y - (h - whole.h) / 2 : whole.y,
    w,
    h,
  };
}

function setView(next) {
  view = next;
  stage.querySelector('svg').setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
}

function select(target) {
  for (const marked of stage.querySelectorAll('.sm-selected')) marked.classList.remove('sm-selected');
  if (!target) {
    showSystem();
    return;
  }
  target.classList.add('sm-selected');
  if (target.dataset.id) showBody(tree.nodes.get(target.dataset.id));
  else {
    const station = [...tree.nodes.values()].flatMap((node) => node.orbit).find((one) => one.id === target.dataset.station);
    if (station) showStation(station);
  }
}

// Each body's surface is painted once and kept, by the body and its size.
const painted = new Map();
const easel = document.createElement('canvas');

/** The address of a body's painted picture, or null where painting is off. */
function pictureFor(item, look) {
  if (new URLSearchParams(location.search).get('plain') === '1') return null;
  const size = pixelsFor(item.r);
  const key = `${item.id}/${size}`;
  if (!painted.has(key)) {
    const made = surface({
      seed: seedOf(item.id), size, family: familyOf(item.node.bodyClass),
      colour: look.colour, star: look.star, halo: look.halo ?? look.colour, dark: look.dark,
    });
    easel.width = size;
    easel.height = size;
    easel.getContext('2d').putImageData(new ImageData(made.data, size, size), 0, 0);
    painted.set(key, easel.toDataURL('image/png'));
  }
  return painted.get(key);
}

function draw(raw) {
  tree = buildTree(readSystem(raw));
  const began = performance.now();
  stage.innerHTML = pictureOf(layoutOf(tree), 28, pictureFor);
  window.systemTrial = { ...(window.systemTrial ?? {}), paintedIn: Math.round(performance.now() - began) };
  const [x, y, w, h] = stage.querySelector('svg').dataset.box.split(' ').map(Number);
  whole = { x, y, w, h };
  setView(opening());
  showSystem();
}

// Moving about: drag to pan, wheel to zoom about the pointer.
let drag = null;
stage.addEventListener('pointerdown', (event) => {
  drag = { x: event.clientX, y: event.clientY, view: { ...view }, moved: false };
});
window.addEventListener('pointermove', (event) => {
  if (!drag) return;
  const dx = event.clientX - drag.x;
  const dy = event.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < 4) return;
  drag.moved = true;
  stage.classList.add('dragging');
  const size = stage.getBoundingClientRect();
  const scale = Math.max(drag.view.w / size.width, drag.view.h / size.height);
  setView({ ...drag.view, x: drag.view.x - dx * scale, y: drag.view.y - dy * scale });
});
window.addEventListener('pointerup', (event) => {
  const wasDrag = drag?.moved;
  drag = null;
  stage.classList.remove('dragging');
  if (wasDrag || !stage.contains(event.target)) return;
  select(event.target.closest('.sm-body, .sm-station'));
});
stage.addEventListener('wheel', (event) => {
  event.preventDefault();
  const factor = event.deltaY > 0 ? 1.18 : 1 / 1.18;
  const w = Math.min(whole.w * 3, Math.max(60, view.w * factor));
  const ratio = w / view.w;
  const size = stage.getBoundingClientRect();
  const px = (event.clientX - size.left) / size.width;
  const py = (event.clientY - size.top) / size.height;
  setView({ x: view.x + view.w * px * (1 - ratio), y: view.y + view.h * py * (1 - ratio), w, h: view.h * ratio });
}, { passive: false });
stage.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const target = event.target.closest('.sm-body, .sm-station');
  if (!target) return;
  event.preventDefault();
  select(target);
});
document.querySelector('#fit').addEventListener('click', () => setView({ ...whole }));

/** The legend's lines for the kinds of ring and belt, in the colours the picture uses. */
function ringLegend() {
  const legend = document.querySelector('#ring-legend');
  for (const [code, word] of Object.entries(RING_KINDS)) {
    const swatch = el('span', null, 'swatch');
    swatch.style.background = `rgb(${RING_COLOURS[code].join(',')})`;
    legend.append(swatch, el('span', `${word[0].toUpperCase()}${word.slice(1)} ring or belt`));
  }
}

async function start() {
  ringLegend();
  const response = await fetch('./data/systems.json');
  if (!response.ok) throw new Error(`The data file data/systems.json did not load (${response.status}).`);
  const data = await response.json();
  for (const raw of data.systems) chooser.append(new Option(raw.system.name, raw.system.id64));
  const wanted = new URLSearchParams(location.search).get('system');
  if (data.systems.some((raw) => raw.system.id64 === wanted)) chooser.value = wanted;
  const show = () => draw(data.systems.find((raw) => raw.system.id64 === chooser.value));
  chooser.addEventListener('change', show);
  show();
  // For looking in from the browser's console.
  window.systemTrial = { ...(window.systemTrial ?? {}), data, tree: () => tree };
}

start().catch((error) => {
  problem.textContent = String(error?.message ?? error);
  console.error(error);
});
