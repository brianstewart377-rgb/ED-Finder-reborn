/**
 * Display wording for Inspect's catalogue codes and Ratings V4 explanations.
 *
 * The V3 routes return governed public codes (`metal_rich`, `high_tech`,
 * `lowtemperaturediamond`) and the Ratings V4 scorer returns rule and feature
 * keys (`elw-agriculture`, `reserve_level`). These helpers only reword them;
 * they never compute or reinterpret a score.
 */
import {
  RATING_ECONOMIES,
  type EconomyRating,
  type RatingContribution,
} from '$lib/api/client';

/** `metal_rich` → `Metal Rich`; empty or missing codes stay null. */
export function titleFromCode(code: string | null | undefined): string | null {
  const words = (code ?? '').split(/[_\s]+/u).filter(Boolean);
  if (words.length === 0) return null;
  return words
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/**
 * The governed ring-hotspot vocabulary (`apps/importer/src/v3_spansh/adapter.py`):
 * the Frontier mining commodity tokens, keyed by their V3 public code.
 */
const HOTSPOT_LABELS: Readonly<Record<string, string>> = {
  alexandrite: 'Alexandrite',
  benitoite: 'Benitoite',
  bromellite: 'Bromellite',
  grandidierite: 'Grandidierite',
  lowtemperaturediamond: 'Low Temperature Diamond',
  monazite: 'Monazite',
  musgravite: 'Musgravite',
  opal: 'Void Opal',
  painite: 'Painite',
  platinum: 'Platinum',
  rhodplumsite: 'Rhodplumsite',
  serendibite: 'Serendibite',
  tritium: 'Tritium',
};

export function hotspotLabel(code: string): string {
  return HOTSPOT_LABELS[code] ?? titleFromCode(code) ?? code;
}

/** `2 Platinum · 1 Painite`, or null when no hotspot is catalogued. */
export function hotspotSummary(
  hotspots: readonly Readonly<{ signalType: string; count: number }>[],
): string | null {
  if (hotspots.length === 0) return null;
  return hotspots
    .map((hotspot) => `${hotspot.count} ${hotspotLabel(hotspot.signalType)}`)
    .join(' · ');
}

/**
 * V3 station-type codes for fleet carriers. The importer files Spansh's
 * `Fleet Carrier` and `Drake-Class Carrier` under one vocabulary row, whose
 * public code is `fleet_carrier` when the vocabulary is seeded in source order
 * (`apps/importer/src/v3_spansh/pipeline.py`). Matching the `carrier` word
 * keeps this right if a database was seeded with the other spelling.
 */
const CARRIER_STATION_TYPE = /(?:^|_)carrier$/u;

/** Whether a catalogued station is a fleet carrier, which can move at any time. */
export function isFleetCarrier(stationType: string | null): boolean {
  return stationType !== null && CARRIER_STATION_TYPE.test(stationType);
}

/** Scorer economy key → display name (`HighTech` → `High Tech`). */
export function ratingEconomyLabel(economy: string): string {
  return economy === 'HighTech' ? 'High Tech' : economy;
}

/** 0–1 → `72%`. */
export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Highest potential first; ties keep the scorer's own economy order. */
export function byPotential<T extends EconomyRating>(
  ratings: readonly T[],
): T[] {
  const order = (economy: string) =>
    (RATING_ECONOMIES as readonly string[]).indexOf(economy);
  return [...ratings].sort(
    (left, right) =>
      right.potential - left.potential ||
      order(left.economy) - order(right.economy),
  );
}

/** Specialisation as a value, or its unresolved range. */
export function specialisationLabel(rating: EconomyRating): string {
  return rating.specialisation !== null
    ? String(rating.specialisation)
    : `unresolved, ${rating.specialisationMin}–${rating.specialisationMax}`;
}

const RULE_SUBJECTS: Readonly<Record<string, string>> = {
  elw: 'Earth-like world',
  'water-world': 'Water world',
  ammonia: 'Ammonia world',
  terraformable: 'Terraformable',
  biologicals: 'Biological signals',
  geologicals: 'Geological signals',
  volcanism: 'Volcanism',
  icy: 'Icy body',
  'tidal-lock': 'Tidally locked',
};

const RULE_ECONOMY_SUFFIX =
  /-(agriculture|hightech|tourism|extraction|industrial|refinery|military)$/u;

/**
 * A strong-link rule key in words: `elw-agriculture` → `Earth-like world`,
 * `reserves-pristine` → `Pristine reserves`. Unknown keys are only de-hyphenated.
 */
export function ruleLabel(rule: string): string {
  const reserves = /^reserves-(.+)$/u.exec(rule);
  if (reserves?.[1]) return `${titleFromCode(reserves[1])} reserves`;
  const subject = rule.replace(RULE_ECONOMY_SUFFIX, '');
  const known = RULE_SUBJECTS[subject];
  if (known) return known;
  const words = subject.replaceAll('-', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const EVIDENCE_LABELS: Readonly<Record<string, string>> = {
  body_class: 'body class',
  spectral_class: 'star class',
  luminosity_class: 'luminosity class',
  body_inventory: 'complete body list',
  reserve_level: 'reserve level',
  tidally_locked: 'tidal lock',
  provenance: 'source record',
};

/** A Ratings V4 evidence feature key in words. */
export function evidenceLabel(feature: string): string {
  return EVIDENCE_LABELS[feature] ?? feature.replaceAll('_', ' ');
}

/**
 * How a body takes part in an economy, in words: `Native (water world)`,
 * `Modifier`, `Native and modifier (high metal content world)`.
 */
export function contributionRole(contribution: RatingContribution): string {
  const role =
    contribution.native && contribution.modifier
      ? 'Native and modifier'
      : contribution.native
        ? 'Native'
        : 'Modifier';
  const bodyClass = contribution.contributors
    .find((contributor) => contributor.startsWith('native:'))
    ?.slice('native:'.length)
    .trim();
  return bodyClass ? `${role} (${bodyClass})` : role;
}

/**
 * The strong links that moved a body's local score, with the scorer's own
 * (capped) totals: `+20 Earth-like world, Biological signals; −10 Icy body`.
 * Null when nothing adjusted the base score.
 */
export function adjustmentSummary(
  contribution: RatingContribution,
): string | null {
  const parts: string[] = [];
  if (contribution.positive > 0 || contribution.positiveRules.length > 0)
    parts.push(
      `+${contribution.positive} ${contribution.positiveRules.map(ruleLabel).join(', ')}`.trim(),
    );
  if (contribution.negative > 0 || contribution.negativeRules.length > 0)
    parts.push(
      `−${contribution.negative} ${contribution.negativeRules.map(ruleLabel).join(', ')}`.trim(),
    );
  return parts.length > 0 ? parts.join('; ') : null;
}
