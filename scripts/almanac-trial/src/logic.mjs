// The trial page's own rules, kept apart from the map so they can be tested
// without a browser: how a candidate from Find becomes a marker, and what is
// said about it.
//
// The page marks Find's first page of results, and can show the other
// candidates around them as plain stars. That is one guess at what the map
// could carry: what it should show in the end is the owner's call.
//
// A star's colour is its star class, by the same rule our own map uses. The
// rule is read straight from the web app, not copied, so the two cannot drift.
import { stellarPresentation } from '../../../apps/web/src/lib/spatial/stellar-presentation.ts';

/** Where a search starts: the two places the trial shows candidates around. */
export const ANCHORS = {
  sol: { name: 'Sol', x: 0, y: 0, z: 0 },
  colonia: { name: 'Colonia', x: -9530.5, y: -910.28125, z: 19808.125 },
};

/** How many results Find shows at a time in our own app (its page size). */
export const FIND_PAGE = 50;

/** The group of Find's results: one calm colour and a plain disc, apart from the stars. */
export const RESULT_GROUP = 'Find results';
const RESULT_CATEGORY = {
  name: RESULT_GROUP,
  color: [70, 225, 215],
  markerStyle: 'disc',
  description: "The first page of Find's answer",
};

/** The group of the marker that shows where a search starts. */
export const ANCHOR_GROUP = 'Search anchor';
const ANCHOR_CATEGORY = {
  name: ANCHOR_GROUP,
  color: [255, 255, 255],
  markerStyle: 'disc',
  description: 'Where the search starts',
};

/**
 * The group and colour of a star, from Find's name for its class ("M (Red
 * dwarf) Star"). Both come from our own map's rule: the group is its name for
 * the family ("M class star") and the colour is the one it draws that family
 * in. A black hole's own colour is black, so it takes the colour of its ring.
 */
export function starGroup(star) {
  const look = stellarPresentation({ type: star });
  const rgb = look.darkCore ? look.haloRgb : look.coreRgb;
  return { name: look.label, color: rgb.map((part) => Math.round(part * 255)) };
}

/**
 * Stars are drawn only when the view is this close or closer. From further
 * out, ten thousand of them are one bright ball that hides Find's results;
 * close up they are a star field. Our own map does the same with its stars.
 */
export const STARS_SHOW_WITHIN_LY = 400;

/** Whether the stars draw at a zoom distance. */
export function starsShownAt(distanceLy) {
  return distanceLy <= STARS_SHOW_WITHIN_LY;
}

/** The star groups among some candidates, the commonest first. */
export function starCategories(rows) {
  const groups = new Map();
  for (const row of rows) {
    const group = starGroup(row.star);
    const held = groups.get(group.name) ?? { ...group, count: 0 };
    held.count += 1;
    groups.set(group.name, held);
  }
  return [...groups.values()]
    .sort((one, other) => other.count - one.count || one.name.localeCompare(other.name))
    .map(({ name, color }) => ({ name, color }));
}

/**
 * One row of Find's answer (`POST /api/local/search`) cut down to what the
 * page shows. Rows with no usable id, name or position are left out (null).
 */
export function trimRow(found) {
  const coords = found?.coords ?? {};
  const position = [coords.x, coords.y, coords.z];
  if (found?.id64 == null || !found.name || !position.every(Number.isFinite)) return null;
  // A number too large to be held exactly would name a different system.
  if (typeof found.id64 === 'number' && !Number.isSafeInteger(found.id64)) return null;
  return {
    id64: String(found.id64),
    name: String(found.name),
    x: position[0],
    y: position[1],
    z: position[2],
    score: Number.isFinite(found.archetype_score) ? found.archetype_score : null,
    tier: found.archetype_tier ?? null,
    archetype: found.primary_archetype ?? null,
    second: found.secondary_archetype ?? null,
    distance: Number.isFinite(found.distance) ? found.distance : null,
    star: found.main_star_type ?? null,
    bodies: Number.isFinite(found.body_count) ? found.body_count : null,
    claim: found.claim_status ?? null,
  };
}

/** The file the page reads, from the rows Find gave. */
export function dataFile(anchor, results, fetchedOn) {
  const seen = new Set();
  const rows = [];
  for (const found of results) {
    const row = trimRow(found);
    if (row && !seen.has(row.id64)) {
      seen.add(row.id64);
      rows.push(row);
    }
  }
  return { anchor, fetchedOn, rows };
}

/** A candidate as the map takes it: in its star's group unless another is named. */
export function toRecord(row, group = starGroup(row.star).name) {
  const record = {
    name: row.name,
    id64: row.id64,
    coords: { x: row.x, y: row.y, z: row.z },
    categories: [group],
  };
  if (row.star) record.primaryStar = row.star;
  if (row.bodies != null) record.bodyCount = row.bodies;
  return record;
}

/** The anchor of a search, as a marker of its own. */
export function anchorRecord(anchor) {
  return {
    name: anchor.name,
    coords: { x: anchor.x, y: anchor.y, z: anchor.z },
    categories: [ANCHOR_GROUP],
  };
}

/** How far out the camera opens on a set: the whole 500 ly ball in view. */
export const OPENING_DISTANCE_LY = 1600;

/**
 * The sets the map's top bar offers. For each anchor: Find's results alone,
 * and the same results among the other candidates drawn as stars.
 */
export const SETS = [
  { id: 'sol', anchor: 'sol', others: false },
  { id: 'sol-stars', anchor: 'sol', others: true },
  { id: 'colonia', anchor: 'colonia', others: false },
  { id: 'colonia-stars', anchor: 'colonia', others: true },
];

/**
 * One set as an entry of the map's own list, without its loader. The map's
 * top bar names the set and its dialog lists them all.
 */
export function datasetEntry(set) {
  const anchor = ANCHORS[set.anchor];
  const results = `the first ${FIND_PAGE} results Find lists within 500 light years of ${anchor.name}`;
  return {
    id: set.id,
    label: set.others ? `Results near ${anchor.name}, among stars` : `Results near ${anchor.name}`,
    collection: 'ED-Finder',
    region: `Within 500 ly of ${anchor.name}`,
    description: set.others
      ? `${results[0].toUpperCase()}${results.slice(1)}, with the rest of Find's first 10,000 candidates `
        + `around them as stars, each in the colour of its star class. The stars show when you zoom in to `
        + `${STARS_SHOW_WITHIN_LY} light years or closer.`
      : `${results[0].toUpperCase()}${results.slice(1)}: one page, as our own app shows it.`,
    view: { cursor: [anchor.x, anchor.y, anchor.z], distance: OPENING_DISTANCE_LY, pitch: 35 },
  };
}

/**
 * What a set's loader hands the map: the groups, the anchor, Find's first
 * page of results, and (when the set asks) the other candidates as stars.
 */
export function contentOf(data, others) {
  const results = data.rows.slice(0, FIND_PAGE);
  const stars = others ? data.rows.slice(FIND_PAGE) : [];
  return {
    categories: [RESULT_CATEGORY, ...starCategories(stars), ANCHOR_CATEGORY],
    systems: [
      anchorRecord(data.anchor),
      ...results.map((row) => toRecord(row, RESULT_GROUP)),
      ...stars.map((row) => toRecord(row)),
    ],
  };
}

function words(code) {
  return String(code).replaceAll('_', ' ');
}

/** Where Inspect shows a system on the live site. */
export function inspectAddress(id64) {
  return `https://ed-finder.app/s/${id64}`;
}

/**
 * What the map's information panel says about a candidate, after the map's
 * own fields (position, distance from Sol, region, star, bodies). The
 * distance from the anchor is left out for Sol: the map already shows it.
 */
export function detailsOf(row, anchorName) {
  const values = [];
  if (row.score != null) values.push({ label: 'SCORE', value: String(Math.round(row.score)) });
  if (row.tier) values.push({ label: 'TIER', value: row.tier });
  if (row.archetype) values.push({ label: 'BEST AS', value: words(row.archetype) });
  if (row.second) values.push({ label: 'ALSO SUITS', value: words(row.second) });
  if (row.distance != null && anchorName !== 'Sol') {
    values.push({ label: `FROM ${anchorName.toUpperCase()}`, value: `${row.distance.toFixed(1)} ly` });
  }
  if (row.claim) values.push({ label: 'CLAIM', value: words(row.claim) });
  values.push({ label: 'ID', value: row.id64, copy: row.id64 });
  return { description: `One of the first 10,000 candidates Find lists within 500 ly of ${anchorName}.`, values };
}

/**
 * Made-up systems in a cube around a point, the same each time, for seeing
 * how many markers the map can carry. Never mixed into the real candidates'
 * groups: they have a group of their own.
 */
export function madeUpRecords(count, centre, edge) {
  let seed = 12345;
  const next = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  return Array.from({ length: count }, (_unused, index) => ({
    name: `Made-up ${index + 1}`,
    coords: {
      x: centre.x + (next() - 0.5) * edge,
      y: centre.y + (next() - 0.5) * edge,
      z: centre.z + (next() - 0.5) * edge,
    },
    categories: ['Made-up'],
  }));
}
