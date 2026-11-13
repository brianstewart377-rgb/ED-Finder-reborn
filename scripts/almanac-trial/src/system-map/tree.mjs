// A system as the System Map trial needs it: what orbits what, what rings
// and belts there are, and what is built at each body. Read from the live
// site's own answers (GET /api/v1/systems/<id>, .../bodies, .../rings and
// .../stations). No drawing here.
//
// Two things our data does not always say, and how the trial handles them.
// Both are marked, so the page can say "placed by distance" and never pass a
// guess off as a fact.
//
// 1. What a body orbits. Second stars, pairs of planets and some moons come
//    with no parent. A body named as the game names a moon ("B 1 d", moon of
//    "B 1") is hung from the body its name points to. Anything else goes in
//    the row of the star it sits nearest to, by distance from the arrival
//    point.
// 2. Which body a station is at. Stations on the ground come with their
//    body. Most stations in orbit do not. Those go to the body whose distance
//    from the arrival point is nearest to theirs, if one is close enough.

/** Station types that are in orbit, and how the map marks each. */
export const ORBITAL_TYPES = {
  coriolis_starport: 'starport',
  orbis_starport: 'starport',
  ocellus_starport: 'starport',
  dodec_starport: 'starport',
  asteroid_base: 'starport',
  outpost: 'outpost',
  megaship: 'megaship',
  space_construction_depot: 'building',
};

/** Station types that are on the ground. */
export const SURFACE_TYPES = new Set([
  'planetary_port', 'planetary_outpost', 'settlement', 'surface_settlement', 'dockable_planet_station',
  'planetary_construction_depot',
]);

/** How near a station's distance must be to a body's for the station to be put there, in light seconds. */
export function nearEnough(bodyLs, stationLs) {
  return Math.abs(bodyLs - stationLs) <= Math.max(3, stationLs * 0.005);
}

function words(code) {
  return String(code ?? 'unknown kind').replaceAll('_', ' ');
}

function number(value) {
  return Number.isFinite(value) ? value : null;
}

/** One body of the site's answer, cut down to what the map uses. */
function readBody(found) {
  return {
    id: String(found.body_id),
    parent: found.direct_parent_body_id == null ? null : String(found.direct_parent_body_id),
    name: String(found.name),
    // The game's own number for the body within its system: 1 for star A, and so on.
    number: Number.isFinite(Number(found.frontier_body_id)) ? Number(found.frontier_body_id) : null,
    type: found.body_type,
    bodyClass: found.body_class ?? null,
    spectral: found.spectral_class ?? null,
    luminosity: found.luminosity_class ?? null,
    mainStar: found.is_main_star === true,
    radiusKm: number(found.radius_km),
    axisKm: number(found.semi_major_axis_km),
    arrivalLs: number(found.distance_from_arrival_ls),
    landable: found.is_landable === true,
  };
}

/**
 * The site's four answers for one system, as one tidy record. Fleet carriers
 * are left out: they come and go, and nobody built them here.
 */
export function readSystem(raw) {
  const bodies = raw.bodies.filter((found) => found.lifecycle_state !== 'DELETED').map(readBody);
  const rings = raw.rings
    .filter((found) => found.lifecycle_state !== 'DELETED')
    .map((found) => ({
      body: String(found.body_id),
      belt: found.kind === 'BELT',
      name: found.name ?? null,
      ringClass: found.ring_type ?? null,
      innerKm: number(found.inner_radius_km),
      outerKm: number(found.outer_radius_km),
    }));
  const stations = raw.stations
    .filter((found) => found.lifecycle_state !== 'DELETED' && found.station_type !== 'fleet_carrier')
    .map((found) => ({
      id: String(found.station_id),
      name: String(found.name),
      type: found.station_type ?? null,
      kind: words(found.station_type),
      body: found.body_id == null ? null : String(found.body_id),
      arrivalLs: number(found.distance_from_arrival_ls),
    }));
  return { id64: String(raw.system.id64), name: String(raw.system.name), bodies, rings, stations };
}

/** A body's name without the system's name in front: "Sol" stays, "X 1 a" becomes "1 a". */
export function shortName(body, systemName) {
  const name = body.name;
  if (name.toLowerCase().startsWith(`${systemName.toLowerCase()} `)) return name.slice(systemName.length + 1);
  return name;
}

/**
 * Brothers and sisters in the order they are met going outward. Where every
 * one has the size of its orbit, that decides. Where one does not (a pair, or
 * a body our data gives no parent), all go by distance from the arrival
 * point, so none is sorted by a number the others lack.
 */
function sortOutward(children) {
  const byOrbit = children.every((child) => child.axisKm != null);
  const key = (child) => (byOrbit ? child.axisKm : child.arrivalLs ?? Infinity);
  children.sort((one, other) => key(one) - key(other) || one.name.localeCompare(other.name));
}

/**
 * What is built at each body, and what could not be put at any body.
 * A station with its body in our data is a fact. One without is put at the
 * body nearest to it by distance from the arrival point, and marked `guess`.
 */
export function placeStations(system) {
  const byBody = new Map(system.bodies.map((body) => [body.id, { orbit: [], ground: [], unknown: [] }]));
  const notPlaced = [];
  const real = system.bodies.filter((body) => body.type !== 'barycentre' && body.arrivalLs != null);
  for (const station of system.stations) {
    const ground = SURFACE_TYPES.has(station.type);
    let body = station.body && byBody.has(station.body) ? station.body : null;
    let guess = false;
    if (!body && station.arrivalLs != null) {
      let best = null;
      for (const candidate of real) {
        const gap = Math.abs(candidate.arrivalLs - station.arrivalLs);
        if (best === null || gap < best.gap) best = { id: candidate.id, gap, ls: candidate.arrivalLs };
      }
      if (best && nearEnough(best.ls, station.arrivalLs)) {
        body = best.id;
        guess = true;
      }
    }
    if (!body) {
      notPlaced.push(station);
      continue;
    }
    // A station whose kind our data does not give is neither claimed for
    // orbit nor for the ground.
    const mark = ORBITAL_TYPES[station.type] ?? null;
    const where = mark ? 'orbit' : ground ? 'ground' : 'unknown';
    byBody.get(body)[where].push({ ...station, mark, guess });
  }
  for (const built of byBody.values()) {
    for (const list of Object.values(built)) list.sort((one, other) => one.name.localeCompare(other.name));
  }
  return { byBody, notPlaced };
}

/**
 * The system as a tree. Each star is the head of a row; everything else
 * hangs from what it orbits. A body with no parent in our data is hung from
 * the body its name points to, or else from the star nearest to it by
 * distance from the arrival point. Either way it is marked: `guessedParent`
 * is 'name' or 'distance', and false for a parent our data gives.
 */
export function buildTree(system) {
  const built = placeStations(system);
  const nodes = new Map(system.bodies.map((body) => [body.id, {
    ...body,
    short: shortName(body, system.name),
    children: [],
    rings: [],
    belts: [],
    guessedParent: false,
    orbit: built.byBody.get(body.id).orbit,
    ground: built.byBody.get(body.id).ground,
    unknownKind: built.byBody.get(body.id).unknown,
  }]));
  for (const ring of system.rings) {
    const node = nodes.get(ring.body);
    if (node) node[ring.belt ? 'belts' : 'rings'].push(ring);
  }
  // Stars head the rows in the game's own order (A, then B), by the game's
  // number for each. That is not always the star a ship arrives at.
  const stars = [...nodes.values()]
    .filter((node) => node.type === 'star' && (node.parent === null || !nodes.has(node.parent)))
    .sort((one, other) => (one.number ?? Infinity) - (other.number ?? Infinity) || one.name.localeCompare(other.name));
  const starsOutward = [...stars].sort((one, other) => (one.arrivalLs ?? 0) - (other.arrivalLs ?? 0));

  // A pair has no distance of its own, so it takes that of its nearest member.
  for (const node of nodes.values()) {
    if (node.arrivalLs != null) continue;
    const members = system.bodies.filter((body) => body.parent === node.id && body.arrivalLs != null);
    if (members.length > 0) node.arrivalLs = Math.min(...members.map((body) => body.arrivalLs));
  }

  const byName = new Map([...nodes.values()].map((node) => [node.name, node]));
  for (const node of nodes.values()) {
    if (stars.includes(node)) continue;
    let parent = node.parent === null ? null : nodes.get(node.parent) ?? null;
    if (!parent) {
      // The game names a moon after its planet: "B 1 d" is a moon of "B 1".
      const named = byName.get(node.name.slice(0, Math.max(0, node.name.lastIndexOf(' '))));
      if (named && named !== node && node.type !== 'barycentre') {
        parent = named;
        node.guessedParent = 'name';
      }
    }
    if (!parent && stars.length > 0) {
      // The star furthest from the arrival point that this body is still beyond.
      const ls = node.arrivalLs ?? 0;
      parent = starsOutward.filter((star) => (star.arrivalLs ?? 0) <= ls).at(-1) ?? starsOutward[0];
      node.guessedParent = 'distance';
    }
    if (parent) parent.children.push(node);
  }
  for (const node of nodes.values()) sortOutward(node.children);
  return { id64: system.id64, name: system.name, stars, nodes, notPlaced: built.notPlaced };
}
