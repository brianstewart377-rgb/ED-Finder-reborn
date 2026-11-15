// Find: suggestions for a system's name, the search for systems near a
// star, and the ranking by colony type, with the checks made on each answer.
// Offered to the app through ./client, like everything in this folder.
import {
  ARCHETYPE_KEYS,
  isArchetypeKey,
  isTier,
} from '$lib/features/explore/archetypes';
import type {
  ArchetypeKey,
  MinimumScore,
  Tier,
} from '$lib/features/explore/archetypes';
import { ApiError } from '@ed-finder/api-client/core';
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';
import type {
  ArchetypeRankingRow,
  ArchetypeRankingsResponse,
  AutocompleteHit,
  AutocompleteResponse,
  LocalSearchRequest,
  RankingIdentity,
  SearchResponse,
  SystemArchetypeExplanationEntry,
  SystemArchetypeExplanationSummary,
  SystemRow,
} from './generated';
import {
  autocompleteApiLocalAutocompleteGet,
  getArchetypeRankingsApiArchetypesRankingsGet,
  getSystemArchetypeExplanationApiArchetypesSystemId64ExplanationGet,
  getSystemArchetypesApiArchetypesSystemId64Get,
  localSearchEndpointApiLocalSearchPost,
} from './generated/sdk.gen';
import { SOL_ID64, SOL_NAME } from './sol';
import { isScore, isUnitInterval, losslessId64 } from './values';

export type AutocompleteSystem = Readonly<
  Pick<
    AutocompleteHit,
    'name' | 'x' | 'y' | 'z' | 'population' | 'primaryEconomy'
  > & { id64: Id64 }
>;

/** The committed anchor's identity, held by the caller alongside the request. */
export type DistanceAnchor = Readonly<{ id64: Id64; name: string }>;

/** Which origin a row's `distance` was measured from. */
export type DistanceReference = Readonly<{
  kind: 'anchor' | 'sol';
  id64: Id64;
  name: string;
}>;

/**
 * The seven provenance fields copied from a ranked response envelope onto
 * every normalized row, so a saved snapshot can be compared like with like
 * (ranking profile and archetype model identities) and the generation that
 * produced it can be shown.
 */
export type RankingProvenance = Readonly<{
  ranking_version: string;
  ranking_sha256: string;
  derived_generation_id: string;
  publication_sequence: number;
  archetype_version: string;
  archetype_manifest_sha256: string;
  archetype_model_sha256: string;
}>;

/** The product identity carried by the ranking card, pinned on expansion. */
export type ArchetypeExplanationIdentity = Readonly<
  Pick<
    RankingProvenance,
    'derived_generation_id' | 'archetype_version' | 'archetype_manifest_sha256'
  >
>;

export type ArchetypeExplanationEntry = Readonly<
  Omit<SystemArchetypeExplanationEntry, 'key'> & { key: ArchetypeKey }
>;

/** Bounded disclosure states: API failures never throw into the result card. */
export type ArchetypeExplanationResult =
  | Readonly<{
      kind: 'explained';
      id64: Id64;
      archetypes: readonly ArchetypeExplanationEntry[];
      completeness: number;
      summary: SystemArchetypeExplanationSummary;
      ranking: RankingProvenance;
    }>
  | Readonly<{ kind: 'explanation_unavailable' }>
  | Readonly<{ kind: 'error'; status: number }>;

/**
 * `unknown` is only the decoder's result for a pre-F4 snapshot carrying the
 * ambiguous generic `archetype_score`; the facade never produces it.
 */
export type ScoreKind = 'overall_potential' | 'selected_fit' | 'unknown';

export type RankingIdentityFields = Readonly<{
  ranking_score: number | null;
  /** API-owned tier of `ranking_score`; carried with `score_kind`. */
  ranking_tier: Tier | null;
  score_kind: ScoreKind;
  selected_archetype: ArchetypeKey | null;
  distance_reference: DistanceReference | null;
  ranking_provenance: RankingProvenance | null;
}>;

export type ExploreSystem = Readonly<
  Pick<
    SystemRow,
    | 'name'
    | 'coords'
    | 'distance'
    | 'population'
    | 'primaryEconomy'
    | 'secondaryEconomy'
    | 'security'
    | 'allegiance'
    | 'government'
    | 'is_colonised'
    | 'is_being_colonised'
    | 'main_star_type'
    | 'main_star_subtype'
    | 'archetype_score'
    | 'archetype_tier'
    | 'primary_archetype'
    | 'secondary_archetype'
    | 'archetype_confidence'
    | 'overall_development_potential'
    | 'buildability_score'
    | 'purity_score'
    | 'build_complexity'
    | 'est_total_slots'
    | 'tags'
    | 'elw_count'
    | 'ww_count'
    | 'ammonia_count'
    | 'landable_count'
    | 'terraformable_count'
    | 'bio_signal_total'
    | 'geo_signal_total'
    | 'confidence'
    | 'completeness'
    | 'claim_status'
    | 'claim_station_seen_at'
  > &
    Pick<ArchetypeRankingRow, 'rival_archetype' | 'rival_score'> & {
      id64: Id64;
    } & Partial<RankingIdentityFields>
>;

/**
 * Exclusive offset ceiling and navigable window applied to every mode: the
 * API counts and pages to 1,000 ("1,000+ found"), so the web app does too.
 */
export const FINDER_NAVIGATION_LIMIT = 1_000;
export const FINDER_PAGE_SIZE_MIN = 1;
export const FINDER_PAGE_SIZE_MAX = 50;
export const FINDER_PAGE_SIZE_DEFAULT = 50;

export type FinderPageTruncation =
  | Readonly<{
      source: 'local-search';
      /** Sentinel-backed: true only when a 1,001st matching row existed. */
      total_is_capped: boolean;
      /**
       * False where the API answered the page before its matches were
       * counted (a slow count must not hold up the rows, nor fail them).
       * `total` is then only how many matches the page shows there to be:
       * its offset plus its rows. The count is asked for again with
       * `wait_for_count`. Absent or true: `total` is the count.
       */
      total_is_counted?: boolean;
    }>
  | Readonly<{
      source: 'rankings';
      /** API-owned: a 1,001st qualifying row existed in canonical order. */
      is_truncated: boolean;
    }>;

/** One normalized page envelope; navigation copy derives only from this. */
export type FinderPageEnvelope = Readonly<{
  page_size: number;
  count: number;
  /** Raw response total (never above 1,000 from either route). */
  total: number;
  /**
   * `min(total, navigation_limit)`. For a full page whose matches were not
   * counted it is one more than the page reaches, so that the next page can
   * be asked for: whether there is one is not known.
   */
  navigable_total: number;
  offset: number;
  navigation_limit: number;
  truncation: FinderPageTruncation;
}>;

/** Find shows systems within this many light years of inhabited space. */
export type ReachLy = 250 | 500;

export type ArchetypeRankingsRequest = Readonly<{
  archetype: ArchetypeKey;
  min_score: MinimumScore;
  /** Facade page size 1..50, mapped to the endpoint's wire `limit`. */
  page_size: number;
  offset: number;
  /** Omitted means the API's default, 250. */
  reach_ly?: ReachLy;
}>;

export type ArchetypeRankingsPage = Readonly<{
  archetype: ArchetypeKey;
  archetype_label: string;
  results: readonly ExploreSystem[];
  page: FinderPageEnvelope;
  ranking: RankingProvenance;
  source?: string | null;
  query_ms?: number | null;
}>;

export type AutocompleteSystemsResponse = Omit<
  AutocompleteResponse,
  'results'
> & {
  readonly results: readonly AutocompleteSystem[];
};

export type ExploreSearchResponse = Omit<SearchResponse, 'results'> & {
  readonly results: readonly ExploreSystem[];
  readonly page: FinderPageEnvelope;
};

export type ExploreSearchRequest = LocalSearchRequest;

export async function autocompleteSystems(
  query: string,
  signal?: AbortSignal,
): Promise<AutocompleteSystemsResponse> {
  const { data } = await autocompleteApiLocalAutocompleteGet({
    throwOnError: true,
    query: { q: query, limit: 10 },
    signal,
  });
  return {
    ...data,
    results: data.results.map((hit) => ({
      ...hit,
      id64: losslessId64(hit.id64),
    })),
  };
}

const finiteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const nonNegativeInteger = (value: unknown): value is number =>
  Number.isInteger(value) && (value as number) >= 0;

/**
 * Copy the seven provenance fields from a response envelope. An envelope that
 * does not carry all seven yields `null`: a row without provenance is listed
 * but never enters a score comparison, which is the honest fallback.
 */
export function normalizeRankingProvenance(
  ranking: Partial<RankingIdentity> | null | undefined,
): RankingProvenance | null {
  if (!ranking || typeof ranking !== 'object') return null;
  const {
    ranking_version,
    ranking_sha256,
    derived_generation_id,
    publication_sequence,
    archetype_version,
    archetype_manifest_sha256,
    archetype_model_sha256,
  } = ranking;
  if (
    typeof ranking_version !== 'string' ||
    typeof ranking_sha256 !== 'string' ||
    typeof derived_generation_id !== 'string' ||
    !nonNegativeInteger(publication_sequence) ||
    typeof archetype_version !== 'string' ||
    typeof archetype_manifest_sha256 !== 'string' ||
    typeof archetype_model_sha256 !== 'string'
  )
    return null;
  return {
    ranking_version,
    ranking_sha256,
    derived_generation_id,
    publication_sequence,
    archetype_version,
    archetype_manifest_sha256,
    archetype_model_sha256,
  };
}

/** An Any search anchored at Sol measures from the same origin as rankings. */
export function distanceReferenceForAnchor(
  anchor: DistanceAnchor | null | undefined,
): DistanceReference | null {
  if (!anchor) return null;
  return anchor.id64 === SOL_ID64
    ? { kind: 'sol', id64: SOL_ID64, name: anchor.name || SOL_NAME }
    : { kind: 'anchor', id64: anchor.id64, name: anchor.name };
}

const SOL_REFERENCE: DistanceReference = {
  kind: 'sol',
  id64: SOL_ID64,
  name: SOL_NAME,
};

function finderPageSize(value: number | undefined, fallback: number): number {
  return Number.isInteger(value) ? (value as number) : fallback;
}

function pageEnvelope(
  pageSize: number,
  offset: number,
  count: number,
  total: number | undefined,
  truncation: FinderPageTruncation,
  /** An uncounted page that came back full: more may follow it. */
  moreMayFollow = false,
): FinderPageEnvelope {
  const rawTotal = nonNegativeInteger(total) ? total : count;
  return {
    page_size: pageSize,
    count,
    total: rawTotal,
    navigable_total: Math.min(
      moreMayFollow ? Math.max(rawTotal, offset + count) + 1 : rawTotal,
      FINDER_NAVIGATION_LIMIT,
    ),
    offset,
    navigation_limit: FINDER_NAVIGATION_LIMIT,
    truncation,
  };
}

/**
 * Normalize one `/api/local/search` (Any) row. The overall score is carried as
 * `ranking_score` with `score_kind: 'overall_potential'`; the distance
 * reference is the committed anchor's identity (which the wire request does
 * not carry, so the caller supplies it), and only when a distance was measured.
 */
export function normalizeAnySearchRow(
  system: SystemRow,
  reference: DistanceReference | null,
  provenance: RankingProvenance | null,
): ExploreSystem {
  const tier = system.archetype_tier;
  // The Any ranking score is the overall potential only: never fall back to a
  // semantically different value under `score_kind: 'overall_potential'`.
  const overall = finiteNumber(system.overall_development_potential)
    ? system.overall_development_potential
    : null;
  // `archetype_tier` is the API's tier of `archetype_score`; it travels with
  // the overall score only when it was derived from that same value.
  const tierOfOverall =
    overall !== null && system.archetype_score === overall && isTier(tier)
      ? tier
      : null;
  // A colony type searched near a system: the row is ranked by that type's
  // fit, carried exactly as a rankings row carries it.
  const selected = system.selected_archetype;
  const selectedFit =
    typeof selected === 'string' &&
    isArchetypeKey(selected) &&
    finiteNumber(system.selected_archetype_score) &&
    isTier(system.selected_archetype_tier)
      ? {
          ranking_score: system.selected_archetype_score,
          ranking_tier: system.selected_archetype_tier,
          score_kind: 'selected_fit' as const,
          selected_archetype: selected,
        }
      : null;
  return {
    ...system,
    id64: losslessId64(system.id64),
    ranking_score: overall,
    ranking_tier: tierOfOverall,
    score_kind: 'overall_potential',
    selected_archetype: null,
    ...selectedFit,
    distance_reference: finiteNumber(system.distance) ? reference : null,
    ranking_provenance: provenance,
  };
}

/**
 * The facade/URL page size of an Any request. It is distinct from the wire
 * `size`, which is clamped to `min(page_size, 1000 - offset)` near the
 * navigation ceiling: Previous/Next must keep stepping by the facade page
 * size, exactly as the rankings path does. Without an explicit value (the
 * live default-off request) the wire size is the page size.
 */
function anyFacadePageSize(
  request: ExploreSearchRequest,
  pageSize: number | undefined,
): number {
  if (pageSize === undefined)
    return finderPageSize(request.size, FINDER_PAGE_SIZE_DEFAULT);
  if (
    !Number.isInteger(pageSize) ||
    pageSize < FINDER_PAGE_SIZE_MIN ||
    pageSize > FINDER_PAGE_SIZE_MAX
  )
    throw new RangeError(
      `page_size must be an integer from ${FINDER_PAGE_SIZE_MIN} to ${FINDER_PAGE_SIZE_MAX}`,
    );
  return pageSize;
}

/**
 * Normalize an Any (`/api/local/search`) response into the lossless page
 * envelope. `reference` is the committed anchor's `{ id64, name }` when the
 * request is anchored (F4 design section 6); pass `null` for galaxy-wide.
 * `pageSize` is the facade/URL page size when it differs from the clamped
 * wire `size`.
 */
export function normalizeAnySearchResponse(
  data: SearchResponse,
  request: ExploreSearchRequest,
  reference: DistanceAnchor | null = null,
  pageSize?: number,
): ExploreSearchResponse {
  const facadePageSize = anyFacadePageSize(request, pageSize);
  const provenance = normalizeRankingProvenance(data.ranking);
  const distanceReference = request.reference_coords
    ? distanceReferenceForAnchor(reference)
    : null;
  const results = data.results.map((system) =>
    normalizeAnySearchRow(system, distanceReference, provenance),
  );
  // The API answered the page before its matches were counted.
  const uncounted = data.total_is_counted === false;
  return {
    ...data,
    results,
    page: pageEnvelope(
      facadePageSize,
      finderPageSize(request.from, 0),
      results.length,
      data.total,
      {
        source: 'local-search',
        total_is_capped: data.total_is_capped === true,
        ...(uncounted ? { total_is_counted: false } : {}),
      },
      uncounted &&
        results.length > 0 &&
        results.length >= finderPageSize(request.size, facadePageSize),
    ),
  };
}

/** True where a page's matches were not counted: its total is no count. */
export function pageIsUncounted(page: FinderPageEnvelope): boolean {
  return (
    page.truncation.source === 'local-search' &&
    page.truncation.total_is_counted === false
  );
}

export async function searchExploreSystems(
  request: ExploreSearchRequest,
  signal?: AbortSignal,
  reference: DistanceAnchor | null = null,
  pageSize?: number,
): Promise<ExploreSearchResponse> {
  // Validate before dispatch so a bad page size never reaches the API.
  const facadePageSize = anyFacadePageSize(request, pageSize);
  const { data } = await localSearchEndpointApiLocalSearchPost({
    throwOnError: true,
    body: request,
    signal,
  });
  return normalizeAnySearchResponse(data, request, reference, facadePageSize);
}

/**
 * Normalize one `/api/archetypes/rankings` row onto the Explore shape. The
 * selected fit is carried as `ranking_score`/`ranking_tier` with
 * `score_kind: 'selected_fit'` and never enters generic `archetype_score`;
 * `distance` is the endpoint's Sol distance with a `sol` reference.
 */
export function normalizeRankingRow(
  row: ArchetypeRankingRow,
  archetype: ArchetypeKey,
  provenance: RankingProvenance | null,
): ExploreSystem {
  if (!finiteNumber(row.score) || !isTier(row.tier))
    throw new TypeError('The archetype ranking row has no valid score/tier');
  const distance = finiteNumber(row.distance_to_sol)
    ? row.distance_to_sol
    : null;
  return {
    id64: losslessId64(row.id64),
    name: row.name,
    coords: row.coords ?? null,
    distance,
    primary_archetype: row.primary_archetype ?? null,
    secondary_archetype: row.secondary_archetype ?? null,
    ...(row.rival_archetype !== undefined
      ? { rival_archetype: row.rival_archetype }
      : {}),
    ...(row.rival_score !== undefined ? { rival_score: row.rival_score } : {}),
    archetype_confidence: row.archetype_confidence ?? null,
    overall_development_potential: row.overall_development_potential ?? null,
    buildability_score: row.buildability_score ?? null,
    purity_score: row.purity_score ?? null,
    build_complexity: row.build_complexity ?? null,
    est_total_slots: row.est_total_slots ?? null,
    tags: row.tags,
    elw_count: row.elw_count ?? null,
    landable_count: row.landable_count ?? null,
    confidence: row.confidence ?? null,
    completeness: row.completeness ?? null,
    claim_status: row.claim_status ?? null,
    claim_station_seen_at: row.claim_station_seen_at ?? null,
    archetype_score: null,
    archetype_tier: null,
    ranking_score: row.score,
    ranking_tier: row.tier,
    score_kind: 'selected_fit',
    selected_archetype: archetype,
    distance_reference: distance === null ? null : SOL_REFERENCE,
    ranking_provenance: provenance,
  };
}

function validateRankingsRequest(request: ArchetypeRankingsRequest): {
  archetype: ArchetypeKey;
  limit: number;
  offset: number;
} {
  if (!isArchetypeKey(request.archetype))
    throw new RangeError(`Unsupported archetype key: ${request.archetype}`);
  if (
    !Number.isInteger(request.page_size) ||
    request.page_size < FINDER_PAGE_SIZE_MIN ||
    request.page_size > FINDER_PAGE_SIZE_MAX
  )
    throw new RangeError(
      `page_size must be an integer from ${FINDER_PAGE_SIZE_MIN} to ${FINDER_PAGE_SIZE_MAX}`,
    );
  if (
    !nonNegativeInteger(request.offset) ||
    request.offset >= FINDER_NAVIGATION_LIMIT
  )
    throw new RangeError(
      `offset must be an integer from 0 below ${FINDER_NAVIGATION_LIMIT}`,
    );
  return {
    archetype: request.archetype,
    // One navigation ceiling for every mode: the wire page never crosses it.
    limit: Math.min(
      request.page_size,
      FINDER_NAVIGATION_LIMIT - request.offset,
    ),
    offset: request.offset,
  };
}

export function normalizeRankingsResponse(
  data: ArchetypeRankingsResponse,
  request: ArchetypeRankingsRequest,
): ArchetypeRankingsPage {
  const { archetype, offset } = validateRankingsRequest(request);
  const provenance = normalizeRankingProvenance(data.ranking);
  if (!provenance)
    throw new TypeError(
      'The archetype ranking response has no ranking identity',
    );
  const results = data.results.map((row) =>
    normalizeRankingRow(row, archetype, provenance),
  );
  const total = nonNegativeInteger(data.total) ? data.total : results.length;
  if (data.is_truncated === true && total === 0)
    throw new TypeError(
      'The archetype ranking response is truncated with no qualifying rows',
    );
  return {
    archetype,
    archetype_label: data.archetype_label,
    results,
    page: pageEnvelope(request.page_size, offset, results.length, total, {
      source: 'rankings',
      is_truncated: data.is_truncated === true,
    }),
    ranking: provenance,
    source: data.source,
    query_ms: data.query_ms,
  };
}

/**
 * One system's published archetype scores (`GET /api/archetypes/system/{id64}`),
 * read through the configured generated client so the wire id64 stays a
 * lossless decimal string. Used by the compare Refresh action.
 */
export async function getSystemArchetypes(
  id64: Id64,
  signal?: AbortSignal,
): Promise<SystemArchetypesWire> {
  const { data } = await getSystemArchetypesApiArchetypesSystemId64Get({
    path: { id64: parseId64(id64) as unknown as number },
    throwOnError: true,
    signal,
  });
  if (!data) throw new Error('Empty system archetypes response');
  return data;
}
export type SystemArchetypesWire = NonNullable<
  Awaited<
    ReturnType<typeof getSystemArchetypesApiArchetypesSystemId64Get>
  >['data']
>;

/**
 * Selected-archetype ranking (`GET /api/archetypes/rankings`). The facade
 * exposes `page_size` 1..50 and maps it to the endpoint's wire `limit`; it
 * accepts no hard filter, so a picked request can never carry one.
 */
export async function getArchetypeRankings(
  request: ArchetypeRankingsRequest,
  signal?: AbortSignal,
): Promise<ArchetypeRankingsPage> {
  const { archetype, limit, offset } = validateRankingsRequest(request);
  const { data } = await getArchetypeRankingsApiArchetypesRankingsGet({
    throwOnError: true,
    query: {
      archetype,
      min_score: request.min_score,
      limit,
      offset,
      ...(request.reach_ly !== undefined ? { reach_ly: request.reach_ly } : {}),
    },
    signal,
  });
  return normalizeRankingsResponse(data, request);
}

/**
 * Replay all eight explanations only for the product behind the visible card.
 * Both the request gate and the successful response echo must match: a product
 * republish between ranking and expansion must never explain a different score.
 */
export async function getArchetypeExplanation(
  id64: Id64,
  expected: ArchetypeExplanationIdentity,
  signal?: AbortSignal,
): Promise<ArchetypeExplanationResult> {
  try {
    if (
      !expected.derived_generation_id ||
      !expected.archetype_version ||
      !expected.archetype_manifest_sha256
    )
      return { kind: 'explanation_unavailable' };

    const { data, response } =
      await getSystemArchetypeExplanationApiArchetypesSystemId64ExplanationGet({
        path: { id64: parseId64(id64) as unknown as number },
        query: {
          expected_generation_id: expected.derived_generation_id,
          expected_archetype_version: expected.archetype_version,
          expected_manifest_sha256: expected.archetype_manifest_sha256,
        },
        throwOnError: false,
        signal,
      });
    if (!data)
      return response?.status === 409
        ? { kind: 'explanation_unavailable' }
        : { kind: 'error', status: response?.status ?? 0 };

    const ranking = normalizeRankingProvenance(data.ranking);
    if (
      !ranking ||
      ranking.derived_generation_id !== expected.derived_generation_id ||
      ranking.archetype_version !== expected.archetype_version ||
      ranking.archetype_manifest_sha256 !== expected.archetype_manifest_sha256
    )
      return { kind: 'explanation_unavailable' };

    const responseId = losslessId64(data.id64);
    if (
      responseId !== id64 ||
      !isUnitInterval(data.completeness) ||
      !Array.isArray(data.archetypes) ||
      data.archetypes.length !== ARCHETYPE_KEYS.length
    )
      return { kind: 'error', status: 0 };

    const archetypes = new Map<ArchetypeKey, ArchetypeExplanationEntry>();
    for (const entry of data.archetypes) {
      if (
        !entry ||
        !isArchetypeKey(entry.key) ||
        !isScore(entry.score) ||
        !isTier(entry.tier) ||
        !isUnitInterval(entry.confidence) ||
        !finiteNumber(entry.weighted_e4) ||
        !entry.explanation ||
        typeof entry.explanation !== 'object' ||
        Array.isArray(entry.explanation) ||
        archetypes.has(entry.key)
      )
        return { kind: 'error', status: 0 };
      archetypes.set(entry.key, { ...entry, key: entry.key });
    }
    return {
      kind: 'explained',
      id64: responseId,
      archetypes: ARCHETYPE_KEYS.map((key) => archetypes.get(key)!),
      completeness: data.completeness,
      summary: data.summary,
      ranking,
    };
  } catch (error) {
    return error instanceof ApiError && error.status === 409
      ? { kind: 'explanation_unavailable' }
      : { kind: 'error', status: error instanceof ApiError ? error.status : 0 };
  }
}
