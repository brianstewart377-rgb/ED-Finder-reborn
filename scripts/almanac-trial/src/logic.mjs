// The trial page's own rules, kept apart from the map so they can be tested
// without a browser.
//
// What the page is for (owner, 13 Nov 2026): a system chosen in Find has a
// "show me on map" button. The map flies to it and shows every real star
// within 15 ly of it, with the 15 ly range drawn. 15 ly is the distance a
// colony can be claimed from.
//
// A star's colour is its star class, by the same rule our own map uses. The
// rule is read straight from the web app, not copied, so the two cannot drift.
import { stellarPresentation } from '../../../apps/web/src/lib/spatial/stellar-presentation.ts';

/** How far around the chosen system the real stars are shown: the claim distance. */
export const RANGE_LY = 15;

/** How far out the camera opens on a chosen system: its whole range in view. */
export const OPENING_DISTANCE_LY = 70;

/** How many of Find's results near each anchor the trial offers to show. */
export const RESULTS_OFFERED = 10;

/** Where Find's searches for the trial start. Each anchor can be shown too. */
export const ANCHORS = {
  sol: { name: 'Sol', x: 0, y: 0, z: 0 },
  colonia: { name: 'Colonia', x: -9530.5, y: -910.28125, z: 19808.125 },
};

/**
 * The group and colour of a star, from its class as Find names it ("M (Red
 * dwarf) Star") or as the galaxy data does ("M3"). Both come from our own
 * map's rule: the group is its name for the family ("M class star") and the
 * colour is the one it draws that family in. A black hole's own colour is
 * black, so it takes the colour of its ring.
 */
export function starGroup(star) {
  const look = stellarPresentation({ type: star });
  const rgb = look.darkCore ? look.haloRgb : look.coreRgb;
  return { name: look.label, color: rgb.map((part) => Math.round(part * 255)) };
}

/** The star groups among some systems, the commonest first. */
export function starCategories(systems) {
  const groups = new Map();
  for (const system of systems) {
    const group = starGroup(system.star);
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

/**
 * The box of space to ask the site for (`GET /api/map/systems`): the range
 * around a point, widened to whole light years, which is how the site files
 * its answers.
 */
export function boxAround(centre, rangeLy = RANGE_LY) {
  return {
    min_x: Math.floor(centre.x - rangeLy), max_x: Math.ceil(centre.x + rangeLy),
    min_y: Math.floor(centre.y - rangeLy), max_y: Math.ceil(centre.y + rangeLy),
    min_z: Math.floor(centre.z - rangeLy), max_z: Math.ceil(centre.z + rangeLy),
  };
}

function apart(one, other) {
  return Math.hypot(one.x - other.x, one.y - other.y, one.z - other.z);
}

/**
 * The real stars in range of a chosen system, nearest first, from the site's
 * answer for the box around it. The box has corners the ball does not, so
 * each star is measured. The chosen system itself is left out.
 */
export function starsInRange(boxSystems, centre, rangeLy = RANGE_LY) {
  const stars = [];
  for (const found of boxSystems) {
    if (found?.id64 == null || !found.name) continue;
    if (![found.x, found.y, found.z].every(Number.isFinite)) continue;
    if (typeof found.id64 === 'number' && !Number.isSafeInteger(found.id64)) continue;
    const id64 = String(found.id64);
    if (id64 === centre.id64) continue;
    const distance = apart(found, centre);
    if (distance > rangeLy) continue;
    stars.push({
      id64, name: String(found.name), x: found.x, y: found.y, z: found.z,
      star: found.main_star_class ?? null, distance,
    });
  }
  return stars.sort((one, other) => one.distance - other.distance || one.name.localeCompare(other.name));
}

/**
 * The site's own record of an anchor (Sol, Colonia) in the box around it:
 * the system of that name, as the chosen system of a showing.
 */
export function anchorInBox(boxSystems, anchor) {
  const found = boxSystems.find((system) => system.name === anchor.name);
  if (!found) return null;
  return {
    id64: String(found.id64), name: found.name, x: found.x, y: found.y, z: found.z,
    star: found.main_star_class ?? null,
  };
}

/**
 * What a system of the player's own can be. The builder will keep this list;
 * in the trial it comes from a file the player writes. Each status is a
 * group on the map with its own switch, so planned systems can be hidden.
 */
export const STATUSES = {
  built: { name: 'Built', color: [235, 235, 245] },
  building: { name: 'Being built', color: [255, 170, 80] },
  planned: { name: 'Planned', color: [120, 190, 255] },
};

/** The id of the set that shows all of the player's systems at once. */
export const MINE_ID = 'my-systems';

/**
 * The player's list as written, checked. Each entry names a system and says
 * whether it is built, being built or planned; one being built may say how
 * far along it is, from 0 to 100.
 */
export function readMine(list) {
  if (!Array.isArray(list)) throw new Error('The list of your systems must be a list in square brackets.');
  return list.map((entry, index) => {
    const where = `Entry ${index + 1} of your systems`;
    if (typeof entry?.name !== 'string' || !entry.name.trim()) throw new Error(`${where} has no name.`);
    if (!Object.hasOwn(STATUSES, entry.status)) {
      throw new Error(`${where} (${entry.name}) needs a status: built, building or planned.`);
    }
    const mine = { name: entry.name.trim(), status: entry.status };
    if (entry.progress != null) {
      if (!Number.isFinite(entry.progress) || entry.progress < 0 || entry.progress > 100) {
        throw new Error(`${where} (${entry.name}) has a progress that is not a number from 0 to 100.`);
      }
      mine.progress = entry.progress;
    }
    return mine;
  });
}

/**
 * The system of exactly this name among the site's answers to a name search
 * (`GET /api/local/autocomplete`), or null. Capitals do not matter.
 */
export function pickByName(results, name) {
  const wanted = name.trim().toLowerCase();
  const found = results.find((result) => String(result.name).toLowerCase() === wanted);
  if (!found || ![found.x, found.y, found.z].every(Number.isFinite)) return null;
  if (typeof found.id64 === 'number' && !Number.isSafeInteger(found.id64)) return null;
  return { id64: String(found.id64), name: found.name, x: found.x, y: found.y, z: found.z };
}

/** The site's own record of a system in the box around it, with its star class. */
export function systemInBox(boxSystems, system) {
  const found = boxSystems.find((one) => String(one.id64) === system.id64);
  return { ...system, star: found?.main_star_class ?? null };
}

/**
 * One "show me on map": the chosen system and the real stars in range of it.
 * `find` is Find's row for the chosen system, or null for an anchor.
 */
export function showing(chosen, boxSystems, near, find = null) {
  return { chosen, near, find, stars: starsInRange(boxSystems, chosen) };
}

/** A "show me on map" of one of the player's own systems. `mine` is its entry in the list. */
export function showingMine(chosen, boxSystems, mine) {
  return { chosen, near: null, find: null, mine, stars: starsInRange(boxSystems, chosen) };
}

/** The chosen system, in its own star's group like every other star. The ball around it marks it. */
function chosenRecord(shown) {
  const { chosen } = shown;
  const record = {
    name: chosen.name,
    id64: chosen.id64,
    coords: { x: chosen.x, y: chosen.y, z: chosen.z },
    categories: [starGroup(chosen.star).name],
  };
  if (chosen.star) record.primaryStar = chosen.star;
  if (shown.find?.bodies != null) record.bodyCount = shown.find.bodies;
  return record;
}

function starRecord(star) {
  const record = {
    name: star.name,
    id64: star.id64,
    coords: { x: star.x, y: star.y, z: star.z },
    categories: [starGroup(star.star).name],
  };
  if (star.star) record.primaryStar = star.star;
  return record;
}

function collectionOf(shown) {
  if (shown.mine) return 'My systems';
  return shown.find ? `Chosen in Find, near ${shown.near}` : 'Where the searches start';
}

/**
 * A showing as an entry of the map's own list of sets, without its loader.
 * In the trial the list stands in for the "show me on map" button of Find.
 */
export function datasetEntry(shown) {
  const { chosen, stars } = shown;
  return {
    id: chosen.id64,
    label: chosen.name,
    collection: collectionOf(shown),
    region: `Within ${RANGE_LY} ly of ${chosen.name}`,
    description: `${chosen.name} and the ${stars.length.toLocaleString('en-GB')} real stars within `
      + `${RANGE_LY} light years of it, each in the colour of its star class. ${RANGE_LY} light years is `
      + 'how far a colony can be claimed from.',
    systemCount: stars.length + 1,
    view: { cursor: [chosen.x, chosen.y, chosen.z], distance: OPENING_DISTANCE_LY, pitch: 35 },
  };
}

/** What a showing's loader hands the map: the star groups, the chosen system and the stars. */
export function contentOf(shown) {
  return {
    categories: starCategories([shown.chosen, ...shown.stars]),
    systems: [chosenRecord(shown), ...shown.stars.map(starRecord)],
  };
}

/** The claim range of the chosen system, as a sphere for the map to draw. */
export function rangeSphere(shown) {
  const { chosen } = shown;
  return {
    name: `${RANGE_LY} ly around ${chosen.name}`,
    position: [chosen.x, chosen.y, chosen.z],
    radius: RANGE_LY,
    color: [255, 170, 80],
    // Fainter than the map's usual 0.18, so the stars inside keep their colours.
    opacity: 0.1,
  };
}

/** The showings that are the player's own, in the order of the player's list. */
export function mineAmong(showings) {
  return showings.filter((shown) => shown.mine);
}

/** All of the player's systems at once, as an entry of the map's list. The camera frames them all. */
export function mineEntry(mine) {
  return {
    id: MINE_ID,
    label: 'All my systems',
    collection: 'My systems',
    description: `Your ${mine.length} systems, each with ${RANGE_LY} light years around it. Built, being built and `
      + 'planned systems are separate groups, so each can be switched off.',
    systemCount: mine.length,
    view: { fit: 'systems', pitch: 35 },
  };
}

/** What the overview hands the map: one group for each status in use, and the systems. */
export function mineContent(mine) {
  const used = new Set(mine.map((shown) => shown.mine.status));
  return {
    categories: Object.entries(STATUSES)
      .filter(([status]) => used.has(status))
      .map(([, group]) => ({ name: group.name, color: group.color, markerStyle: 'disc' })),
    systems: mine.map((shown) => {
      const record = {
        name: shown.chosen.name,
        id64: shown.chosen.id64,
        coords: { x: shown.chosen.x, y: shown.chosen.y, z: shown.chosen.z },
        categories: [STATUSES[shown.mine.status].name],
      };
      if (shown.chosen.star) record.primaryStar = shown.chosen.star;
      return record;
    }),
  };
}

/** What the panel says of one of the player's systems: its status first. */
function mineValues(mine) {
  const values = [{ label: 'STATUS', value: STATUSES[mine.status].name.toLowerCase() }];
  if (mine.progress != null) values.push({ label: 'PROGRESS', value: `${Math.round(mine.progress)} %` });
  return values;
}

function words(code) {
  return String(code).replaceAll('_', ' ');
}

/** Where Inspect shows a system on the live site. */
export function inspectAddress(id64) {
  return `https://ed-finder.app/s/${id64}`;
}

function aboutChosen(shown) {
  if (shown.mine) return 'One of your systems.';
  return shown.find ? `Chosen in Find, near ${shown.near}.` : `Where the searches near ${shown.near} start.`;
}

/**
 * What the map's information panel says about a system of a showing, after
 * the map's own fields (position, distance from Sol, region, star). The
 * chosen system gets what Find said of it; a star gets how far it is from
 * the chosen system.
 */
export function detailsOf(shown, id64) {
  const { chosen, find } = shown;
  if (id64 === chosen.id64) {
    const values = shown.mine ? mineValues(shown.mine) : [];
    if (find?.score != null) values.push({ label: 'SCORE', value: String(Math.round(find.score)) });
    if (find?.tier) values.push({ label: 'TIER', value: find.tier });
    if (find?.archetype) values.push({ label: 'BEST AS', value: words(find.archetype) });
    if (find?.second) values.push({ label: 'ALSO SUITS', value: words(find.second) });
    if (find?.claim) values.push({ label: 'CLAIM', value: words(find.claim) });
    values.push({ label: 'STARS IN RANGE', value: String(shown.stars.length) });
    values.push({ label: 'ID', value: chosen.id64, copy: chosen.id64 });
    return { description: `${aboutChosen(shown)} The shaded ball is ${RANGE_LY} ly around it.`, values };
  }
  const star = shown.stars.find((one) => one.id64 === id64);
  if (!star) return null;
  return {
    description: `A real star within ${RANGE_LY} ly of ${chosen.name}.`,
    values: [
      { label: `FROM ${chosen.name.toUpperCase()}`, value: `${star.distance.toFixed(1)} ly` },
      { label: 'ID', value: star.id64, copy: star.id64 },
    ],
  };
}
