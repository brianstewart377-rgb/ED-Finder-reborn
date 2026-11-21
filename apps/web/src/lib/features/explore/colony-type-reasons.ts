import type { ArchetypeExplanationEntry } from '$lib/api/client';
import { archetypeLabel } from './archetypes';

/**
 * Why a colony type got its score, in plain words. It only puts the API's own
 * explanation into sentences: no score is worked out here.
 */
export type ColonyTypeReason = Readonly<{
  /** "Stronghold needs Military and Industrial." */
  needs: string;
  /** One line per economy the type uses, with what the system has of it. */
  economies: readonly string[];
  /** How those make the score, one step per line. Empty when not known. */
  steps: readonly string[];
}>;

/** The API's economy numbers. */
const ECONOMIES: Readonly<Record<number, string>> = {
  1: 'Agriculture',
  2: 'Refinery',
  3: 'Industrial',
  4: 'High Tech',
  5: 'Military',
  6: 'Tourism',
  7: 'Extraction',
};

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const numeric = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const ordinals = (value: unknown): number[] =>
  Array.isArray(value)
    ? value.filter(
        (ordinal): ordinal is number =>
          numeric(ordinal) && Object.hasOwn(ECONOMIES, ordinal),
      )
    : [];

/** "A", "A and B", "A, B and C". */
function list(names: readonly string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}
const names = (economies: readonly number[]): string =>
  list(economies.map((ordinal) => ECONOMIES[ordinal]));
const points = (value: number): string =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 });
const times = (value: number): string => value.toFixed(2);

const COUNT_WORDS: Readonly<Record<number, string>> = {
  1: 'one',
  2: 'two',
  3: 'three',
  4: 'four',
};

/** "Industrial: 85 out of 100, from 26 bodies that suit it." */
function economyLine(
  ordinal: number,
  strength: unknown,
  count: unknown,
): string {
  if (!numeric(strength)) return `${ECONOMIES[ordinal]}: not known.`;
  if (strength === 0 || count === 0)
    return `${ECONOMIES[ordinal]}: ${strength} out of 100. No body here suits it.`;
  return numeric(count)
    ? `${ECONOMIES[ordinal]}: ${strength} out of 100, from ${count} ${count === 1 ? 'body that suits' : 'bodies that suit'} it.`
    : `${ECONOMIES[ordinal]}: ${strength} out of 100.`;
}

/**
 * Flexible scores a system's strongest economies, whichever they are: each
 * one's score times the same quality and bodies factors the other colony
 * types use, then the three together, the weakest counting most.
 */
function flexibleReason(
  label: string,
  details: Record<string, unknown>,
): ColonyTypeReason {
  const chosen = ordinals(details.economies);
  const components = record(details.components);
  const potential = record(components.pot);
  const sites = record(components.opp_count);
  const strength = record(components.strength);
  const howMany = COUNT_WORDS[chosen.length];
  return {
    needs: howMany
      ? `${label} needs ${howMany} strong economies, of any kind. Its ${howMany} strongest here:`
      : `${label}: what it needs is not known.`,
    economies: chosen.map((ordinal) => {
      const line = economyLine(ordinal, potential[ordinal], sites[ordinal]);
      const counts = strength[ordinal];
      return numeric(counts) && counts > 0
        ? `${line} With quality and bodies: ${points(counts)}.`
        : line;
    }),
    steps:
      howMany && numeric(details.core)
        ? [
            `${points(details.core)} for the ${howMany} together. The weakest counts most.`,
          ]
        : [],
  };
}

export function colonyTypeReason(
  entry: ArchetypeExplanationEntry,
): ColonyTypeReason {
  const label = archetypeLabel(entry.key) ?? 'This colony type';
  const details = record(entry.explanation);
  if (entry.key === 'flexible') return flexibleReason(label, details);

  const anchors = ordinals(details.anchors);
  const supporting = ordinals(details.supporting);
  const components = record(details.components);
  const potential = record(components.pot);
  const sites = record(components.opp_count);
  const them = anchors.length === 1 ? 'it' : 'them';

  const economies = [
    ...anchors.map((ordinal) =>
      economyLine(ordinal, potential[ordinal], sites[ordinal]),
    ),
    ...supporting.map((ordinal) =>
      numeric(potential[ordinal])
        ? `${ECONOMIES[ordinal]}: ${potential[ordinal]} out of 100.`
        : `${ECONOMIES[ordinal]}: not known.`,
    ),
  ];

  const { core, spec, cap, synergy } = details;
  const pool = [...anchors, ...supporting];
  const steps =
    numeric(core) && numeric(spec) && numeric(cap)
      ? [
          anchors.length === 1
            ? `${points(core)} for how strong ${names(anchors)} is.`
            : `${points(core)} for how strong ${names(anchors)} are. The weakest counts most.`,
          `× ${times(spec)} for quality: how cleanly a colony here can specialise in ${them}.`,
          `× ${times(cap)} for bodies: how many suit ${them}, and how well. 8 or more for each gives the most.`,
          ...(numeric(synergy) && synergy > 0
            ? [
                `+ ${points(synergy)} because ${names(pool)} ${
                  pool.length === 1
                    ? 'is'
                    : pool.length === 2
                      ? 'are both'
                      : 'are all'
                } 60 or more.`,
              ]
            : []),
        ]
      : [];

  return {
    needs:
      anchors.length === 0
        ? `${label}: what it needs is not known.`
        : `${label} needs ${names(anchors)}.${
            supporting.length
              ? ` ${names(supporting)} ${supporting.length === 1 ? 'adds' : 'add'} a bonus when strong too.`
              : ''
          }`,
    economies,
    steps,
  };
}
