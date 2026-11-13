// The trial page's own rules, kept apart from the map so they can be tested
// without a browser: how a candidate from Find becomes a marker, and what is
// said about it.

/** Where a search starts: the two places the trial shows candidates around. */
export const ANCHORS = {
  sol: { name: 'Sol', x: 0, y: 0, z: 0 },
  colonia: { name: 'Colonia', x: -9530.5, y: -910.28125, z: 19808.125 },
};

/**
 * The marker groups: what else a candidate suits, after its best use. On
 * 13 Nov 2026 the 10,000 candidates Find listed near Sol all had the same
 * score (100), tier (S) and best use (flexible), so those cannot tell them
 * apart on a map. The second use can: there are seven.
 */
export const GROUPS = [
  { second: 'stronghold', name: 'Also a stronghold', color: [255, 120, 100] },
  { second: 'manufacturing_hub', name: 'Also a manufacturing hub', color: [255, 190, 80] },
  { second: 'megacomplex', name: 'Also a megacomplex', color: [200, 150, 255] },
  { second: 'research_hub', name: 'Also a research hub', color: [100, 200, 255] },
  { second: 'mining_hub', name: 'Also a mining hub', color: [190, 200, 120] },
  { second: 'paradise', name: 'Also a paradise', color: [120, 235, 160] },
  { second: 'population_capital', name: 'Also a population capital', color: [255, 150, 220] },
];

const GROUP_BY_SECOND = new Map(GROUPS.map((group) => [group.second, group.name]));
export const OTHER_GROUP = 'No second use';

/** The marker group of a candidate. */
export function categoryOf(row) {
  return GROUP_BY_SECOND.get(row.second) ?? OTHER_GROUP;
}

/** The groups the map is told about before any candidate is added. */
export function categories() {
  return [
    ...GROUPS.map((group) => ({ name: group.name, color: group.color })),
    { name: OTHER_GROUP, color: [150, 150, 150] },
    { name: 'Anchor', color: [255, 255, 255], markerStyle: 'disc', description: 'Where a search starts' },
  ];
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

/** A candidate as the map takes it. */
export function toRecord(row) {
  const record = {
    name: row.name,
    id64: row.id64,
    coords: { x: row.x, y: row.y, z: row.z },
    categories: [categoryOf(row)],
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
    categories: ['Anchor'],
  };
}

/** How far out the camera opens on a set: the whole 500 ly ball in view. */
export const OPENING_DISTANCE_LY = 1600;

/**
 * One anchor's candidates as an entry of the map's own list of sets, without
 * its loader. The map's top bar names the set and its dialog lists them all.
 */
export function datasetEntry(key, anchor) {
  return {
    id: key,
    label: `Candidates near ${anchor.name}`,
    collection: 'ED-Finder',
    region: `Within 500 ly of ${anchor.name}`,
    description: `The first 10,000 candidates Find lists within 500 light years of ${anchor.name}, `
      + 'fetched from the live site. Each is grouped by what else it suits, after its best use.',
    view: { cursor: [anchor.x, anchor.y, anchor.z], distance: OPENING_DISTANCE_LY, pitch: 35 },
  };
}

/** What a set's loader hands the map: the groups, the anchor and the candidates. */
export function contentOf(data) {
  return {
    categories: categories(),
    systems: [anchorRecord(data.anchor), ...data.rows.map(toRecord)],
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
  return { description: `One of the candidates Find lists within 500 ly of ${anchorName}.`, values };
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
