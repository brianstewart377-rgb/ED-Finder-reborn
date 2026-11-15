/**
 * Real systems for the System Map's tests: the live site's own answers on
 * 14 November 2026, recorded unchanged (each file's "what" says where and
 * when), with the builder's list of each system's bodies. Only the tests
 * read these.
 *
 *   Sol        one star, 39 planets and moons, a pair (Pluto and Charon),
 *              rings, a belt, and stations with and without a body
 *   LHS 3447   two stars, gas giants with moons, fleet carriers, and
 *              predicted ground slots
 *   Polaris    two stars; two moons our data gives no parent
 *   Altair     two high-metal-content worlds, at 591 K and 1,753 K
 *   Maia       (15 November) two stars far apart, with six pairs: two of
 *              planets around a star, three of moons around a brown dwarf,
 *              and one of a brown dwarf with a planet
 *   Alpha Centauri  (15 November) two stars that circle each other, three
 *              planets that go around both, and a third star far away
 *
 * And one older answer: the builder's list for LHS 3447 on 13 November,
 * when it still lacked some bodies' facts and so predicted nothing for them.
 */
import {
  systemDetailFromV3,
  type BodyV1,
  type PlanBodiesV1,
  type RingV1,
  type StationV1,
  type SystemDetail,
  type SystemV1,
} from '$lib/api/client';
import lhs3447On13Nov from './fixtures/plan-lhs3447-13nov.json';
import alphaCentauri from './fixtures/system-alpha-centauri.json';
import altair from './fixtures/system-altair.json';
import lhs3447 from './fixtures/system-lhs3447.json';
import maia from './fixtures/system-maia.json';
import polaris from './fixtures/system-polaris.json';
import sol from './fixtures/system-sol.json';
import {
  buildTree,
  systemFromDetail,
  type MapNode,
  type MapTree,
} from './tree';

type Recorded = Readonly<{
  what: string;
  system: unknown;
  bodies: readonly unknown[];
  rings: readonly unknown[];
  stations: readonly unknown[];
  plan?: unknown;
}>;

export const RECORDED = {
  sol,
  lhs3447,
  polaris,
  altair,
  maia,
  alphaCentauri,
} as const satisfies Record<string, Recorded>;
export type RecordedName = keyof typeof RECORDED;

/** The raw answers of one recorded system. */
export function answersOf(name: RecordedName): Recorded {
  return RECORDED[name];
}

/** One recorded system as Inspect reads it. */
export function detailOf(name: RecordedName): SystemDetail {
  const raw = answersOf(name);
  return systemDetailFromV3(
    raw.system as SystemV1,
    raw.bodies as BodyV1[],
    raw.rings as RingV1[],
    raw.stations as StationV1[],
  );
}

/** The builder's list of the system's bodies, where it was recorded. */
export function planOf(name: RecordedName): PlanBodiesV1 | null {
  return (answersOf(name).plan as PlanBodiesV1 | undefined) ?? null;
}

/** One recorded system as the map reads it, with the builder's list where recorded. */
export function treeOf(name: RecordedName, withPlan = true): MapTree {
  return buildTree(
    systemFromDetail(detailOf(name), withPlan ? planOf(name) : null),
  );
}

/** LHS 3447 with the builder's list of 13 November, which lacked some facts. */
export function lhs3447WithOlderPlan(): MapTree {
  return buildTree(
    systemFromDetail(
      detailOf('lhs3447'),
      lhs3447On13Nov.plan as unknown as PlanBodiesV1,
    ),
  );
}

/** A body of a tree, by its name. */
export function nodeNamed(tree: MapTree, name: string): MapNode {
  const found = [...tree.nodes.values()].find((node) => node.name === name);
  if (!found) throw new Error(`no body named ${name}`);
  return found;
}
