// The seven economy potentials of a system (Ratings V4) and why each is what
// it is. Offered to the app through ./client, like everything in this folder.
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';
import type {
  CandidateContributionResponse,
  RatingExplanationResponse,
  RatingSummaryResponse,
  SystemExplanationResponse,
  SystemRatingsResponse,
} from './generated';
import {
  systemExplanationApiRatingsV4SystemsSystemId64ExplanationGet,
  systemScoresApiRatingsV4SystemsSystemId64Get,
} from './generated/sdk.gen';
import { isScore, isUnitInterval } from './values';

/**
 * Ratings V4 (frozen scorer 4.0.0): seven independent economy potentials per
 * system from the published derived generation. This is raw potential only;
 * Finder ranking and archetype judgement are separate layers that the route
 * deliberately omits (`apps/api/src/routers/ratings_v4.py`).
 */
export const RATING_ECONOMIES = [
  'Agriculture',
  'Refinery',
  'Industrial',
  'HighTech',
  'Military',
  'Tourism',
  'Extraction',
] as const;

/** The published derived generation a set of ratings was read from. */
export type RatingsIdentity = Readonly<{
  derivedGenerationId: string;
  publicationSequence: string;
  scorerVersion: string;
  mechanicsVersion: string;
}>;

/** One economy's Ratings V4 summary. */
export type EconomyRating = Readonly<{
  /** Scorer economy key, one of {@link RATING_ECONOMIES}. */
  economy: string;
  /** Raw potential, 0–100. */
  potential: number;
  /** Resolved specialisation quality, or null when only its range is known. */
  specialisation: number | null;
  specialisationMin: number;
  specialisationMax: number;
  /** Share of the evidence this rating needs that the catalogue knows, 0–1. */
  completeness: number;
  /** Rule-based confidence, 0–1. Not an empirically measured probability. */
  confidence: number;
}>;

export type SystemRatings = Readonly<{
  identity: RatingsIdentity;
  /** All seven economies, in scorer order. */
  ratings: readonly EconomyRating[];
}>;

/** One of the (at most four) bodies whose local scores make up a potential. */
export type RatingContribution = Readonly<{
  /** Canonical body key: the scorer's candidate id. */
  bodyKey: string;
  native: boolean;
  modifier: boolean;
  base: number;
  positive: number;
  negative: number;
  local: number;
  /** Weight of this local score in the system potential, 0–1. */
  weight: number;
  positiveRules: readonly string[];
  negativeRules: readonly string[];
  /** Scorer contributors, e.g. `native:water world`, `modifier:tourism`. */
  contributors: readonly string[];
  /** Evidence features the catalogue does not know for this body. */
  unknownEvidence: readonly string[];
}>;

export type EconomyRatingExplanation = EconomyRating &
  Readonly<{ contributions: readonly RatingContribution[] }>;

/**
 * A replayed explanation, or `republished` when the published generation
 * changed after the summary was read: an explanation is only shown for the
 * exact ratings on screen.
 */
export type SystemRatingExplanation =
  | Readonly<{
      kind: 'explained';
      identity: RatingsIdentity;
      economies: readonly EconomyRatingExplanation[];
    }>
  | Readonly<{ kind: 'republished'; identity: RatingsIdentity }>;

function ratingsIdentity(
  response: SystemRatingsResponse | SystemExplanationResponse,
): RatingsIdentity {
  return {
    derivedGenerationId: String(response.derived_generation_id),
    publicationSequence: String(response.publication_sequence),
    scorerVersion: String(response.scorer_version),
    mechanicsVersion: String(response.mechanics_version),
  };
}

function economyRating(row: RatingSummaryResponse): EconomyRating {
  if (
    !isScore(row.potential_score) ||
    !isScore(row.specialisation_quality_min) ||
    !isScore(row.specialisation_quality_max) ||
    row.specialisation_quality_min > row.specialisation_quality_max ||
    (row.specialisation_quality !== null &&
      !isScore(row.specialisation_quality)) ||
    !isUnitInterval(row.evidence_completeness) ||
    !isUnitInterval(row.confidence)
  )
    throw new TypeError(
      `The Ratings V4 row for ${String(row.economy)} is out of range`,
    );
  return {
    economy: row.economy,
    potential: row.potential_score,
    specialisation: row.specialisation_quality,
    specialisationMin: row.specialisation_quality_min,
    specialisationMax: row.specialisation_quality_max,
    completeness: row.evidence_completeness,
    confidence: row.confidence,
  };
}

/** Exactly the seven scorer economies, once each, in scorer order. */
function scorerOrder<T extends EconomyRating>(rows: readonly T[]): T[] {
  const byEconomy = new Map(rows.map((row) => [row.economy, row]));
  if (
    rows.length !== RATING_ECONOMIES.length ||
    byEconomy.size !== RATING_ECONOMIES.length
  )
    throw new TypeError(
      'Ratings V4 must return each of the seven economies once',
    );
  return RATING_ECONOMIES.map((economy) => {
    const row = byEconomy.get(economy);
    if (!row) throw new TypeError(`Ratings V4 omitted the ${economy} economy`);
    return row;
  });
}

function ratingContribution(
  row: CandidateContributionResponse,
): RatingContribution {
  if (
    !isScore(row.local_score) ||
    !isUnitInterval(row.system_weight) ||
    !Number.isInteger(row.base_score) ||
    !Number.isInteger(row.positive_adjustment) ||
    !Number.isInteger(row.negative_adjustment)
  )
    throw new TypeError(
      `The Ratings V4 contribution for ${String(row.candidate_id)} is out of range`,
    );
  return {
    bodyKey: String(row.candidate_id),
    native: row.native,
    modifier: row.modifier,
    base: row.base_score,
    positive: row.positive_adjustment,
    negative: row.negative_adjustment,
    local: row.local_score,
    weight: row.system_weight,
    positiveRules: [...row.positive_rules],
    negativeRules: [...row.negative_rules],
    contributors: [...row.contributors],
    unknownEvidence: [
      ...new Set(
        row.evidence
          .filter((feature) => !feature.known)
          .map((feature) => feature.feature_type),
      ),
    ],
  };
}

/** A system's seven Ratings V4 economy potentials (404 when it is unrated). */
export async function getSystemRatings(
  id64: Id64,
  signal?: AbortSignal,
): Promise<SystemRatings> {
  const { data } = await systemScoresApiRatingsV4SystemsSystemId64Get({
    path: { system_id64: parseId64(id64) as unknown as number },
    throwOnError: true,
    signal,
  });
  return {
    identity: ratingsIdentity(data),
    ratings: scorerOrder(data.ratings.map(economyRating)),
  };
}

/**
 * The replayed explanation behind {@link getSystemRatings}, only for the
 * generation those ratings came from (`expected`). A different published
 * generation answers `republished` instead of an explanation that would not
 * match the scores on screen.
 */
export async function getSystemRatingExplanation(
  id64: Id64,
  expected: RatingsIdentity,
  signal?: AbortSignal,
): Promise<SystemRatingExplanation> {
  const { data } =
    await systemExplanationApiRatingsV4SystemsSystemId64ExplanationGet({
      path: { system_id64: parseId64(id64) as unknown as number },
      throwOnError: true,
      signal,
    });
  const identity = ratingsIdentity(data);
  if (
    identity.derivedGenerationId !== expected.derivedGenerationId ||
    identity.publicationSequence !== expected.publicationSequence
  )
    return { kind: 'republished', identity };
  return {
    kind: 'explained',
    identity,
    economies: scorerOrder(data.ratings.map(economyExplanation)),
  };
}

function economyExplanation(
  row: RatingExplanationResponse,
): EconomyRatingExplanation {
  return {
    ...economyRating(row),
    contributions: row.contributions.map(ratingContribution),
  };
}
