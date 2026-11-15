/**
 * A system as the System Map needs it: what orbits what, what rings and belts
 * there are, and what is built at each body. Built from Inspect's own system
 * detail (`getSystem`). No drawing here.
 *
 * Ported from the System Map trial page (`tree.mjs` in
 * `scripts/almanac-trial`, removed once it was in the site: git history has
 * it), which the owner tried on 14 November 2026.
 *
 * Two things our data does not always say, and how the map handles them.
 * Both are marked, so the page can say "placed by distance" and never pass a
 * guess off as a fact.
 *
 * 1. What a body orbits. Second stars, pairs of planets and some moons come
 *    with no parent. A body named as the game names a moon ("B 1 d", moon of
 *    "B 1") is hung from the body its name points to. Anything else goes in
 *    the row of the star it sits nearest to, by distance from the arrival
 *    point.
 * 2. Which body a station is at. Stations on the ground come with their
 *    body. Most stations in orbit do not. Those go to the body whose distance
 *    from the arrival point is nearest to theirs, if one is close enough.
 *
 * A third thing is never a fact from the game at all: how many ground slots
 * a body has. The game publishes no slot counts. The site's builder predicts
 * them, and only when it holds every fact its rule needs; the count is
 * carried here with its reason, so the page can say "predicted" or say why
 * there is no number.
 */
import type {
  PlanBodiesV1,
  PlanSystemBodyV1,
  SystemDetail,
} from '$lib/api/client';
import { isFleetCarrier } from '$lib/components/system-detail-format';

/** How each station type in orbit is marked on the map. */
export type StationMark = 'starport' | 'outpost' | 'megaship' | 'building';

/** Station types that are in orbit, and how the map marks each. */
export const ORBITAL_TYPES: Readonly<Record<string, StationMark>> = {
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
export const SURFACE_TYPES: ReadonlySet<string> = new Set([
  'planetary_port',
  'planetary_outpost',
  'settlement',
  'surface_settlement',
  'dockable_planet_station',
  'planetary_construction_depot',
]);

/** How our data names each kind of ring and belt, in plain words. */
export const RING_KINDS: Readonly<Record<string, string>> = {
  icy: 'icy',
  rocky: 'rocky',
  metal_rich: 'metal rich',
  metallic: 'metallic',
};

export type GroundSlotsWhy =
  'predicted' | 'facts missing' | 'cannot land' | 'no prediction' | 'not asked';

/** What the site's builder says of one body's ground slots. */
export type GroundSlots = Readonly<{
  count: number | null;
  why: GroundSlotsWhy;
  missing: readonly string[];
}>;

export type MapBody = {
  /** The body's canonical key. */
  id: string;
  parent: string | null;
  name: string;
  /** The game's own number for the body within its system (Frontier BodyID). */
  number: number | null;
  type: string | null;
  bodyClass: string | null;
  spectral: string | null;
  luminosity: string | null;
  /** Surface temperature in kelvin, where our data gives it. */
  surfaceK: number | null;
  mainStar: boolean;
  radiusKm: number | null;
  axisKm: number | null;
  arrivalLs: number | null;
  landable: boolean;
  groundSlots: GroundSlots;
};

export type MapRing = Readonly<{
  body: string;
  belt: boolean;
  name: string | null;
  ringClass: string | null;
  innerKm: number | null;
  outerKm: number | null;
}>;

export type MapStation = Readonly<{
  id: string;
  name: string;
  type: string | null;
  kind: string;
  body: string | null;
  arrivalLs: number | null;
}>;

export type PlacedStation = MapStation &
  Readonly<{ mark: StationMark | null; guess: boolean }>;

export type MapSystem = Readonly<{
  id64: string;
  name: string;
  bodies: readonly MapBody[];
  rings: readonly MapRing[];
  stations: readonly MapStation[];
}>;

export type GuessedParent = false | 'name' | 'distance';

export type MapNode = MapBody & {
  short: string;
  children: MapNode[];
  rings: MapRing[];
  belts: MapRing[];
  guessedParent: GuessedParent;
  orbit: PlacedStation[];
  ground: PlacedStation[];
  unknownKind: PlacedStation[];
  ringWords: string | null;
};

export type MapTree = Readonly<{
  id64: string;
  name: string;
  /**
   * The heads of the rows: the stars, in the game's order. A system whose
   * catalogue holds no star has its bodies without a parent as heads
   * instead, so that nothing is left out of the picture.
   */
  heads: readonly MapNode[];
  nodes: ReadonlyMap<string, MapNode>;
  notPlaced: readonly MapStation[];
}>;

/** How near a station's distance must be to a body's for the station to be put there, in light seconds. */
export function nearEnough(bodyLs: number, stationLs: number): boolean {
  return Math.abs(bodyLs - stationLs) <= Math.max(3, stationLs * 0.005);
}

/** A ring's or belt's kind in plain words, or null when our data gives none we know. */
export function ringKind(ring: Pick<MapRing, 'ringClass'>): string | null {
  return (ring.ringClass && RING_KINDS[ring.ringClass]) || null;
}

/**
 * A planet's rings in a few words, from the inside out: "icy ring",
 * "2 metal rich rings", "rings: metal rich, icy". Null when it has none.
 */
export function ringsInWords(rings: readonly MapRing[]): string | null {
  if (rings.length === 0) return null;
  const kinds = rings.map((ring) => ringKind(ring));
  if (kinds.every((kind) => kind === null))
    return rings.length === 1
      ? 'ring, kind not known'
      : `${rings.length} rings, kind not known`;
  if (rings.length === 1) return `${kinds[0]} ring`;
  if (new Set(kinds).size === 1) return `${rings.length} ${kinds[0]} rings`;
  return `rings: ${kinds.map((kind) => kind ?? 'not known').join(', ')}`;
}

/**
 * What the site's builder says of one body's ground slots: the count it
 * predicts, or null with the reason there is none.
 *   'predicted'      the site's rule gave a count
 *   'facts missing'  the rule lacks a fact about the body, named in `missing`
 *   'cannot land'    the body cannot be landed on
 *   'no prediction'  the site gave none, for another reason
 *   'not asked'      the builder's list was not fetched for this system
 */
export function groundSlotsOf(
  found: PlanSystemBodyV1 | undefined,
  asked = found !== undefined,
): GroundSlots {
  if (!asked) return { count: null, why: 'not asked', missing: [] };
  if (!found) return { count: null, why: 'no prediction', missing: [] };
  if (
    found.prediction_status === 'predicted' &&
    Number.isInteger(found.predicted_surface_slots)
  )
    return {
      count: found.predicted_surface_slots as number,
      why: 'predicted',
      missing: [],
    };
  if (found.prediction_status === 'unknown')
    return {
      count: null,
      why: 'facts missing',
      missing: [...(found.missing_inputs ?? [])],
    };
  if (found.is_landable === false)
    return { count: null, why: 'cannot land', missing: [] };
  return { count: null, why: 'no prediction', missing: [] };
}

function words(code: string | null): string {
  return String(code ?? 'unknown kind').replaceAll('_', ' ');
}

function finite(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Inspect's system detail as the map reads it, with the builder's list of the
 * same bodies (`plan`) where it was fetched: that list adds each body's
 * surface temperature and predicted ground slots.
 *
 * Belt clusters are not drawn: their belt is, from its ring row. A station
 * our data puts in a belt cluster is put at the body the belt circles. Fleet
 * carriers are left out: they come and go, and nobody built them here.
 */
export function systemFromDetail(
  detail: SystemDetail,
  plan: PlanBodiesV1 | null = null,
): MapSystem {
  const planned = new Map(
    (plan?.bodies ?? []).map((found) => [found.body_id, found]),
  );
  const clusters = new Map(
    detail.bodies
      .filter((body) => body.bodyType === 'belt_cluster')
      .map((body) => [body.bodyKey, body.directParentBodyKey]),
  );
  const bodies = detail.bodies.filter(
    (body) => body.bodyType !== 'belt_cluster',
  );
  return {
    id64: String(detail.id64),
    name: detail.name,
    bodies: bodies.map((body) => {
      const found = planned.get(body.bodyKey);
      return {
        id: body.bodyKey,
        parent: body.directParentBodyKey,
        name: body.name,
        number: finite(body.id),
        type: body.bodyType,
        bodyClass: body.bodyClass,
        spectral: body.spectralClass,
        luminosity: body.luminosityClass,
        surfaceK: finite(found?.surface_temperature_k),
        mainStar: body.isMainStar === true,
        radiusKm: finite(body.radiusKm),
        axisKm: finite(body.semiMajorAxisKm),
        arrivalLs: finite(body.distanceFromArrivalLs),
        landable: body.isLandable === true,
        groundSlots: groundSlotsOf(found, plan !== null),
      };
    }),
    rings: bodies.flatMap((body) =>
      body.rings.map((ring) => ({
        body: body.bodyKey,
        belt: ring.kind === 'BELT',
        name: ring.name ?? null,
        ringClass: ring.ringType,
        innerKm: finite(ring.innerRadiusKm),
        outerKm: finite(ring.outerRadiusKm),
      })),
    ),
    stations: detail.stations
      .filter((station) => !isFleetCarrier(station.stationType))
      .map((station) => ({
        id: station.stationKey,
        name: station.name,
        type: station.stationType,
        kind: words(station.stationType),
        body:
          station.bodyKey !== null && clusters.has(station.bodyKey)
            ? (clusters.get(station.bodyKey) ?? null)
            : station.bodyKey,
        arrivalLs: finite(station.distanceFromArrivalLs),
      })),
  };
}

/** A body's name without the system's name in front: "Sol" stays, "X 1 a" becomes "1 a". */
export function shortName(body: { name: string }, systemName: string): string {
  const { name } = body;
  if (name.toLowerCase().startsWith(`${systemName.toLowerCase()} `))
    return name.slice(systemName.length + 1);
  return name;
}

/**
 * Brothers and sisters in the order they are met going outward. Where every
 * one has the size of its orbit, that decides. Where one does not (a pair, or
 * a body our data gives no parent), all go by distance from the arrival
 * point, so none is sorted by a number the others lack.
 */
function sortOutward(children: MapNode[]): void {
  const byOrbit = children.every((child) => child.axisKm != null);
  const key = (child: MapNode) =>
    byOrbit ? (child.axisKm as number) : (child.arrivalLs ?? Infinity);
  children.sort(
    (one, other) => key(one) - key(other) || one.name.localeCompare(other.name),
  );
}

type Built = {
  orbit: PlacedStation[];
  ground: PlacedStation[];
  unknown: PlacedStation[];
};

/**
 * What is built at each body, and what could not be put at any body.
 * A station with its body in our data is a fact. One without is put at the
 * body nearest to it by distance from the arrival point, and marked `guess`.
 */
export function placeStations(system: MapSystem): {
  byBody: Map<string, Built>;
  notPlaced: MapStation[];
} {
  const byBody = new Map<string, Built>(
    system.bodies.map((body) => [
      body.id,
      { orbit: [], ground: [], unknown: [] },
    ]),
  );
  const notPlaced: MapStation[] = [];
  const real = system.bodies.filter(
    (body) => body.type !== 'barycentre' && body.arrivalLs != null,
  );
  for (const station of system.stations) {
    const ground = station.type !== null && SURFACE_TYPES.has(station.type);
    let body = station.body && byBody.has(station.body) ? station.body : null;
    let guess = false;
    if (!body && station.arrivalLs != null) {
      let best: { id: string; gap: number; ls: number } | null = null;
      for (const candidate of real) {
        const ls = candidate.arrivalLs as number;
        const gap = Math.abs(ls - station.arrivalLs);
        if (best === null || gap < best.gap)
          best = { id: candidate.id, gap, ls };
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
    const mark = (station.type && ORBITAL_TYPES[station.type]) || null;
    const where = mark ? 'orbit' : ground ? 'ground' : 'unknown';
    byBody.get(body)?.[where].push({ ...station, mark, guess });
  }
  for (const built of byBody.values())
    for (const list of Object.values(built))
      list.sort((one, other) => one.name.localeCompare(other.name));
  return { byBody, notPlaced };
}

/**
 * The system as a tree. Each star is the head of a row; everything else
 * hangs from what it orbits. A body with no parent in our data is hung from
 * the body its name points to, or else from the star nearest to it by
 * distance from the arrival point. Either way it is marked: `guessedParent`
 * is 'name' or 'distance', and false for a parent our data gives.
 */
export function buildTree(system: MapSystem): MapTree {
  const built = placeStations(system);
  const nodes = new Map<string, MapNode>(
    system.bodies.map((body) => {
      const here = built.byBody.get(body.id) as Built;
      return [
        body.id,
        {
          ...body,
          short: shortName(body, system.name),
          children: [],
          rings: [],
          belts: [],
          guessedParent: false,
          orbit: here.orbit,
          ground: here.ground,
          unknownKind: here.unknown,
          ringWords: null,
        },
      ];
    }),
  );
  for (const ring of system.rings) {
    const node = nodes.get(ring.body);
    if (node) node[ring.belt ? 'belts' : 'rings'].push(ring);
  }
  // A planet's rings from the inside out, and what to call them.
  for (const node of nodes.values()) {
    node.rings.sort(
      (one, other) => (one.innerKm ?? Infinity) - (other.innerKm ?? Infinity),
    );
    node.ringWords = ringsInWords(node.rings);
  }
  // Stars head the rows in the game's own order (A, then B), by the game's
  // number for each. That is not always the star a ship arrives at.
  const stars = [...nodes.values()]
    .filter(
      (node) =>
        node.type === 'star' &&
        (node.parent === null || !nodes.has(node.parent)),
    )
    .sort(
      (one, other) =>
        (one.number ?? Infinity) - (other.number ?? Infinity) ||
        one.name.localeCompare(other.name),
    );
  const starsOutward = [...stars].sort(
    (one, other) => (one.arrivalLs ?? 0) - (other.arrivalLs ?? 0),
  );

  // A pair has no distance of its own, so it takes that of its nearest member.
  for (const node of nodes.values()) {
    if (node.arrivalLs != null) continue;
    const members = system.bodies.filter(
      (body) => body.parent === node.id && body.arrivalLs != null,
    );
    if (members.length > 0)
      node.arrivalLs = Math.min(
        ...members.map((body) => body.arrivalLs as number),
      );
  }

  const byName = new Map([...nodes.values()].map((node) => [node.name, node]));
  const attached = new Set<MapNode>();
  for (const node of nodes.values()) {
    if (stars.includes(node)) continue;
    let parent = node.parent === null ? null : (nodes.get(node.parent) ?? null);
    if (!parent) {
      // The game names a moon after its planet: "B 1 d" is a moon of "B 1".
      const named = byName.get(
        node.name.slice(0, Math.max(0, node.name.lastIndexOf(' '))),
      );
      if (named && named !== node && node.type !== 'barycentre') {
        parent = named;
        node.guessedParent = 'name';
      }
    }
    if (!parent && stars.length > 0) {
      // The star furthest from the arrival point that this body is still beyond.
      const ls = node.arrivalLs ?? 0;
      parent =
        starsOutward.filter((star) => (star.arrivalLs ?? 0) <= ls).at(-1) ??
        starsOutward[0];
      node.guessedParent = 'distance';
    }
    if (parent) {
      parent.children.push(node);
      attached.add(node);
    }
  }
  for (const node of nodes.values()) sortOutward(node.children);
  const heads =
    stars.length > 0
      ? stars
      : [...nodes.values()]
          .filter((node) => !attached.has(node))
          .sort((one, other) => (one.arrivalLs ?? 0) - (other.arrivalLs ?? 0));
  return {
    id64: system.id64,
    name: system.name,
    heads,
    nodes,
    notPlaced: built.notPlaced,
  };
}
