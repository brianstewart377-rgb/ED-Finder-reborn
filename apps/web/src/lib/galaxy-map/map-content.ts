/**
 * What the galaxy map is given to draw, kept apart from the map itself so it
 * can be tested without a graphics card.
 *
 * What the map is for (owner, 13 Nov 2026): a system chosen in Find is shown
 * on the map. The map flies to it and shows every real star within 15 ly of
 * it, each in the colour of its star class, with the 15 ly range drawn. 15 ly
 * is the distance a colony can be claimed from. The map does not show Find's
 * results.
 *
 * "My journeys" (owner, 14 Nov 2026) are the systems the signed-in player
 * has visited, from their imported journals, as one quiet group of markers.
 *
 * A star's colour is its star class, by the rule the rest of the site uses
 * (`$lib/spatial/stellar-presentation`), read directly so the two cannot
 * drift apart.
 */
import { stellarPresentation } from '$lib/spatial/stellar-presentation';

/** How far around the chosen system the real stars are shown: the claim distance. */
export const RANGE_LY = 15;
/** How far out the camera stops over a chosen system: its whole range in view. */
export const OPENING_DISTANCE_LY = 70;
/** The camera's angle above the galactic plane over a chosen system, in degrees. */
export const OPENING_PITCH_DEG = 35;
/** How many systems to ask the site for in the box around a chosen system. */
export const STARS_ASKED_FOR = 10_000;
/** The most visited systems asked for at once: the API's own limit. */
export const JOURNEYS_ASKED_FOR = 20_000;
/** The group a visited system belongs to on the map. */
export const JOURNEYS_GROUP = 'My journeys';
/** A quiet blue-grey: on the map, strong colour is for star classes. */
export const JOURNEYS_COLOUR: Rgb = [150, 172, 204];
/** The colour of the 15 ly ball around the chosen system. */
export const RANGE_COLOUR: Rgb = [255, 170, 80];

export type Rgb = readonly [number, number, number];

/** A system as the map needs it. `star` is its class as our data names it. */
export type MapSystem = Readonly<{
  id64: string;
  name: string;
  x: number;
  y: number;
  z: number;
  star: string | null;
}>;

export type StarInRange = MapSystem & Readonly<{ distance: number }>;

/** What Find said of the chosen system. Every part may be missing. */
export type FindFacts = Readonly<{
  potential?: number | null;
  primary?: string | null;
  secondary?: string | null;
  claim?: string | null;
}>;

/** One "show on map": the chosen system and the real stars in range of it. */
export type Showing = Readonly<{
  chosen: MapSystem;
  facts: FindFacts | null;
  /** Nearest first. Empty until the site has answered. */
  stars: readonly StarInRange[];
  starsState: 'loading' | 'ready' | 'failed';
  /** The site held more systems in the box than it returns at once. */
  starsCutShort: boolean;
}>;

/** One system the player has visited. */
export type Journey = Readonly<{
  id64: string;
  name: string;
  x: number;
  y: number;
  z: number;
  visits: number;
  firstVisit: string | null;
  lastVisit: string | null;
}>;

export type MapGroup = Readonly<{
  name: string;
  color: Rgb;
  description?: string;
  markerStyle?: 'glow' | 'disc';
}>;

export type MapRecord = Readonly<{
  name: string;
  id64: string;
  coords: Readonly<{ x: number; y: number; z: number }>;
  categories: readonly string[];
  primaryStar?: string;
}>;

export type MapSphere = Readonly<{
  name: string;
  position: readonly [number, number, number];
  radius: number;
  color: Rgb;
  opacity: number;
}>;

export type MapContent = Readonly<{
  groups: readonly MapGroup[];
  records: readonly MapRecord[];
  spheres: readonly MapSphere[];
}>;

export type DetailValue = Readonly<{
  label: string;
  value: string;
  copy?: string;
}>;

export type MapDetails = Readonly<{
  /** What the system is to the player: decides which buttons it gets. */
  kind: 'chosen' | 'star' | 'journey';
  description: string;
  values: readonly DetailValue[];
}>;

/**
 * The group and colour of a star, from its class as Find names it ("M (Red
 * dwarf) Star") or as the galaxy data does ("M3"). A black hole's own colour
 * is black, so it takes the colour of its ring.
 */
export function starGroup(star: string | null | undefined): MapGroup {
  const look = stellarPresentation({ type: star });
  const rgb = look.darkCore ? look.haloRgb : look.coreRgb;
  return {
    name: look.label,
    color: [
      Math.round(rgb[0] * 255),
      Math.round(rgb[1] * 255),
      Math.round(rgb[2] * 255),
    ],
  };
}

/** The star groups among some systems, the commonest first, each once. */
export function starGroups(
  systems: readonly Pick<MapSystem, 'star'>[],
): MapGroup[] {
  const groups = new Map<string, MapGroup & { count: number }>();
  for (const system of systems) {
    const group = starGroup(system.star);
    const held = groups.get(group.name) ?? { ...group, count: 0 };
    held.count += 1;
    groups.set(group.name, held);
  }
  return [...groups.values()]
    .sort(
      (one, other) =>
        other.count - one.count || one.name.localeCompare(other.name),
    )
    .map(({ name, color }) => ({ name, color }));
}

/**
 * The box of space to ask the site for (`GET /api/map/systems`): the range
 * around a point, widened to whole light years, which is how the site files
 * its answers.
 */
export function boxAround(
  centre: Readonly<{ x: number; y: number; z: number }>,
  rangeLy = RANGE_LY,
) {
  return {
    minX: Math.floor(centre.x - rangeLy),
    maxX: Math.ceil(centre.x + rangeLy),
    minY: Math.floor(centre.y - rangeLy),
    maxY: Math.ceil(centre.y + rangeLy),
    minZ: Math.floor(centre.z - rangeLy),
    maxZ: Math.ceil(centre.z + rangeLy),
    limit: STARS_ASKED_FOR,
  };
}

/** A system of the site's answer for a box, as the answer names its fields. */
export type BoxSystem = Readonly<{
  id64: string;
  name: string;
  x: number;
  y: number;
  z: number;
  mainStarClass?: string | null;
}>;

/**
 * The real stars in range of a chosen system, nearest first, from the site's
 * answer for the box around it. The box has corners the ball does not, so
 * each star is measured. The chosen system itself is left out.
 */
export function starsInRange(
  box: readonly BoxSystem[],
  chosen: Pick<MapSystem, 'id64' | 'x' | 'y' | 'z'>,
  rangeLy = RANGE_LY,
): StarInRange[] {
  const stars: StarInRange[] = [];
  for (const found of box) {
    if (!found?.id64 || !found.name) continue;
    if (![found.x, found.y, found.z].every(Number.isFinite)) continue;
    const id64 = String(found.id64);
    if (id64 === chosen.id64) continue;
    const distance = Math.hypot(
      found.x - chosen.x,
      found.y - chosen.y,
      found.z - chosen.z,
    );
    if (distance > rangeLy) continue;
    stars.push({
      id64,
      name: String(found.name),
      x: found.x,
      y: found.y,
      z: found.z,
      star: found.mainStarClass ?? null,
      distance,
    });
  }
  return stars.sort(
    (one, other) =>
      one.distance - other.distance || one.name.localeCompare(other.name),
  );
}

/** The chosen system's own star class in the site's answer for its box, if it is there. */
export function starClassInBox(
  box: readonly BoxSystem[],
  id64: string,
): string | null {
  return (
    box.find((system) => String(system.id64) === id64)?.mainStarClass ?? null
  );
}

/** Sol's id. Our data stores an unknown position as 0, 0, 0, which is Sol's. */
const SOL_ID64 = '10477373803';

/**
 * A system chosen in Find, as the map needs it, or null where its position
 * is not known: a system with no position cannot be shown, and must never be
 * drawn on top of Sol.
 */
export function chosenFromFind(
  system:
    | Readonly<{
        id64: string;
        name?: string | null;
        coords?: Readonly<{
          x?: number | null;
          y?: number | null;
          z?: number | null;
        }> | null;
        main_star_type?: string | null;
      }>
    | null
    | undefined,
): MapSystem | null {
  const coords = system?.coords;
  if (!system || !coords) return null;
  const { x, y, z } = coords;
  if (
    typeof x !== 'number' ||
    typeof y !== 'number' ||
    typeof z !== 'number' ||
    ![x, y, z].every(Number.isFinite)
  )
    return null;
  if (x === 0 && y === 0 && z === 0 && system.id64 !== SOL_ID64) return null;
  return {
    id64: system.id64,
    name: system.name?.trim() || `System ${system.id64}`,
    x,
    y,
    z,
    star: system.main_star_type ?? null,
  };
}

/** The claim range of the chosen system, as a ball for the map to draw. */
export function rangeSphere(chosen: MapSystem): MapSphere {
  return {
    name: `${RANGE_LY} ly around ${chosen.name}`,
    position: [chosen.x, chosen.y, chosen.z],
    radius: RANGE_LY,
    color: RANGE_COLOUR,
    // Fainter than the map's usual 0.18, so the stars inside keep their colours.
    opacity: 0.1,
  };
}

function record(system: MapSystem, groups: readonly string[]): MapRecord {
  const made: MapRecord = {
    name: system.name,
    id64: system.id64,
    coords: { x: system.x, y: system.y, z: system.z },
    categories: groups,
  };
  return system.star ? { ...made, primaryStar: system.star } : made;
}

/**
 * Everything the map is handed: the chosen system first, then each real star
 * in range in its own star's group, then the systems the player has visited.
 * A visited system that is also a star in range is one marker, in its star's
 * colour, and counts in both groups.
 *
 * The groups come in an order that only ever grows at its end as a showing
 * fills in: the player's journeys (which stay as systems are chosen), then
 * the chosen system's own star class, then the other classes, the commonest
 * first. So when a system's stars arrive they can be added to what is drawn
 * (`whatToAdd`) with nothing taken off the map first.
 */
export function mapContent(
  showing: Showing | null,
  journeys: readonly Journey[] | null,
): MapContent {
  const shown = showing ? [showing.chosen, ...showing.stars] : [];
  const visited = new Set((journeys ?? []).map((journey) => journey.id64));
  const groups: MapGroup[] = [];
  if (journeys)
    groups.push({
      name: JOURNEYS_GROUP,
      color: JOURNEYS_COLOUR,
      description:
        'Systems you have visited, from your imported journals. Only you see these.',
      markerStyle: 'disc',
    });
  if (showing) {
    const own = starGroup(showing.chosen.star);
    groups.push(
      own,
      ...starGroups(shown).filter((group) => group.name !== own.name),
    );
  }
  const records: MapRecord[] = shown.map((system) =>
    record(
      system,
      visited.has(system.id64)
        ? [starGroup(system.star).name, JOURNEYS_GROUP]
        : [starGroup(system.star).name],
    ),
  );
  if (journeys) {
    const drawn = new Set(shown.map((system) => system.id64));
    for (const journey of journeys) {
      if (drawn.has(journey.id64)) continue;
      drawn.add(journey.id64);
      records.push(record({ ...journey, star: null }, [JOURNEYS_GROUP]));
    }
  }
  return {
    groups,
    records,
    spheres: showing ? [rangeSphere(showing.chosen)] : [],
  };
}

const same = (one: unknown, other: unknown) =>
  JSON.stringify(one) === JSON.stringify(other);

/**
 * What to add to the map to turn what is drawn into `next`, when nothing has
 * to come off it: the groups to add at the end, the systems that are new or
 * changed (the map replaces a system it already holds), and whether the
 * shaded balls differ. Null when a system or a group has gone, or the groups
 * would come in another order: for those the map can only be emptied whole
 * and filled again.
 */
export function whatToAdd(
  drawn: MapContent,
  next: MapContent,
): { groups: MapGroup[]; records: MapRecord[]; spheres: boolean } | null {
  if (drawn.groups.length > next.groups.length) return null;
  if (drawn.groups.some((group, at) => !same(group, next.groups[at])))
    return null;
  const before = new Map(
    drawn.records.map((record) => [record.id64, JSON.stringify(record)]),
  );
  const after = new Set(next.records.map((record) => record.id64));
  for (const id64 of before.keys()) if (!after.has(id64)) return null;
  return {
    groups: next.groups.slice(drawn.groups.length),
    records: next.records.filter(
      (record) => before.get(record.id64) !== JSON.stringify(record),
    ),
    spheres: !same(drawn.spheres, next.spheres),
  };
}

const words = (code: string) => code.replaceAll('_', ' ');

function day(stamp: string | null): string | null {
  if (!stamp) return null;
  const when = new Date(stamp);
  return Number.isNaN(when.getTime())
    ? null
    : when.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      });
}

function visitValues(journey: Journey | undefined): DetailValue[] {
  if (!journey) return [];
  const values: DetailValue[] = [
    { label: 'YOUR VISITS', value: journey.visits.toLocaleString('en-GB') },
  ];
  const first = day(journey.firstVisit);
  const last = day(journey.lastVisit);
  if (first) values.push({ label: 'FIRST VISIT', value: first });
  if (last && last !== first) values.push({ label: 'LAST VISIT', value: last });
  return values;
}

function starsInRangeValue(showing: Showing): string {
  if (showing.starsState === 'loading') return 'looking…';
  if (showing.starsState === 'failed') return 'did not load';
  const count = showing.stars.length.toLocaleString('en-GB');
  return showing.starsCutShort ? `${count} or more` : count;
}

const id = (id64: string): DetailValue => ({
  label: 'ID',
  value: id64,
  copy: id64,
});

/**
 * What the map's information panel says about a system, after the map's own
 * fields (position, distance from Sol, region, star). The chosen system gets
 * what Find said of it; a star gets how far it is from the chosen system; a
 * visited system gets the player's visits. Null for a system the map was not
 * given.
 */
export function detailsFor(
  showing: Showing | null,
  journeys: readonly Journey[] | null,
  id64: string,
): MapDetails | null {
  const journey = journeys?.find((one) => one.id64 === id64);
  if (showing && id64 === showing.chosen.id64) {
    const { facts } = showing;
    const values: DetailValue[] = [];
    if (typeof facts?.potential === 'number')
      values.push({
        label: 'BEST COLONY POTENTIAL',
        value: String(Math.round(facts.potential)),
      });
    if (facts?.primary)
      values.push({ label: 'BEST AS', value: words(facts.primary) });
    if (facts?.secondary)
      values.push({ label: 'ALSO SUITS', value: words(facts.secondary) });
    if (facts?.claim)
      values.push({ label: 'CLAIM', value: words(facts.claim) });
    values.push({
      label: 'STARS IN RANGE',
      value: starsInRangeValue(showing),
    });
    values.push(...visitValues(journey), id(id64));
    return {
      kind: 'chosen',
      description: `The system chosen in Find. The shaded ball is ${RANGE_LY} ly around it: how far a colony can be claimed from.`,
      values,
    };
  }
  const star = showing?.stars.find((one) => one.id64 === id64);
  if (showing && star)
    return {
      kind: 'star',
      description: `A real star within ${RANGE_LY} ly of ${showing.chosen.name}.`,
      values: [
        {
          label: `FROM ${showing.chosen.name.toUpperCase()}`,
          value: `${star.distance.toFixed(1)} ly`,
        },
        ...visitValues(journey),
        id(id64),
      ],
    };
  if (journey)
    return {
      kind: 'journey',
      description: 'A system you have visited, from your imported journals.',
      values: [...visitValues(journey), id(id64)],
    };
  return null;
}

/** What the page says in words about the showing, for every reader. */
export function showingInWords(showing: Showing | null): string {
  if (!showing) return 'Select a result to show it on the map.';
  const { chosen, stars } = showing;
  if (showing.starsState === 'loading')
    return `Flying to ${chosen.name}. Looking for the real stars within ${RANGE_LY} ly of it…`;
  if (showing.starsState === 'failed')
    return `${chosen.name} is on the map. The stars within ${RANGE_LY} ly of it did not load.`;
  const count = stars.length.toLocaleString('en-GB');
  if (showing.starsCutShort)
    return `${chosen.name} and ${count} of the real stars within ${RANGE_LY} ly of it: more than the site returns at once.`;
  return stars.length === 1
    ? `${chosen.name} and the 1 real star within ${RANGE_LY} ly of it.`
    : `${chosen.name} and the ${count} real stars within ${RANGE_LY} ly of it.`;
}
