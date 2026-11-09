/**
 * Finder ranking state and its URL codec (F4a, section 6 of
 * docs/superpowers/specs/2026-10-10-v3-finder-f4-archetype-ui-design.md).
 *
 * The URL is the source of truth for shareable ranking state. This module is
 * pure: it parses and canonicalizes `archetype`, `min-tier`, `page_size`,
 * `offset` (and, once slice 2 defines the contract, `weight`), decides which
 * request a state produces, and applies the one exclusive 10,000 navigation
 * ceiling to every mode *from the URL alone, before any request is issued*.
 * Rendering, history effects and hydration live in the Explore feature behind
 * the default-off `FINDER_F4_ENABLED` gate.
 */
import type {
  ArchetypeRankingsRequest,
  ExploreSearchRequest,
  FinderPageEnvelope,
} from '$lib/api/client';
import {
  ANY_ARCHETYPE,
  DEFAULT_MINIMUM_TIER,
  isArchetypeKey,
  minimumScoreForTier,
  parseMinimumTier,
  type ArchetypeKey,
  type SelectedMinimumTier,
} from './archetypes';
import {
  DEFAULT_SEARCH_RADIUS,
  normalizeSearchRadius,
  type SearchRadius,
} from './search-state';

export type FinderRankingState = Readonly<{
  archetype: typeof ANY_ARCHETYPE | ArchetypeKey;
  /** Selected-archetype-only; `null` is mandatory in Any until slice 1d. */
  minimumTier: SelectedMinimumTier | null;
  /** Facade `page_size`, integer 1..50; default 50. */
  pageSize: number;
  /** Non-negative integer below `NAVIGATION_LIMIT`. */
  offset: number;
  /** Empty until slice 2 defines the V3 weight contract. */
  weights: Readonly<Record<string, number>>;
}>;

export const PAGE_SIZE_MIN = 1;
export const PAGE_SIZE_MAX = 50;
export const DEFAULT_PAGE_SIZE = 50;
/** Exclusive offset ceiling and navigable window for every mode. */
export const NAVIGATION_LIMIT = 10_000;

/** Today's live Explore page size, preserved exactly in a disabled build. */
export const LEGACY_EXPLORE_PAGE_SIZE = 24;

export const RANKING_URL_PARAMETERS = [
  'archetype',
  'min-tier',
  'page_size',
  'offset',
  'weight',
] as const;
export type RankingUrlParameter = (typeof RANKING_URL_PARAMETERS)[number];

/** Passive selection and detail-overlay parameters are independent of ranking. */
export const SELECTION_URL_PARAMETER = 'selected';
export const DETAIL_URL_PARAMETER = 'system';

/**
 * Slice 2 has not defined the weight dimensions or total rules, so weights are
 * neither parsed nor emitted. Flip only with that contract.
 */
export const WEIGHT_CONTRACT_DEFINED = false;
export const WEIGHT_BASIS_POINTS_MAX = 10_000;

export const DEFAULT_RANKING_STATE: FinderRankingState = Object.freeze({
  archetype: ANY_ARCHETYPE,
  minimumTier: null,
  pageSize: DEFAULT_PAGE_SIZE,
  offset: 0,
  weights: Object.freeze({}),
});

const isInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value);

export function isValidPageSize(value: unknown): value is number {
  return isInteger(value) && value >= PAGE_SIZE_MIN && value <= PAGE_SIZE_MAX;
}

/** The last page-aligned offset that still addresses a row of `navigableTotal`. */
export function lastValidOffset(
  navigableTotal: number,
  pageSize: number,
): number {
  const size = isValidPageSize(pageSize) ? pageSize : DEFAULT_PAGE_SIZE;
  const total = Math.min(
    NAVIGATION_LIMIT,
    isInteger(navigableTotal) ? navigableTotal : 0,
  );
  if (total <= 0) return 0;
  return Math.floor((total - 1) / size) * size;
}

/**
 * A positive result set can shrink underneath a shared or restored page URL.
 * Return its last valid page, or null when no replacement is necessary. Zero
 * results are a genuine empty state, even when the requested offset is nonzero.
 *
 * Call only for the fresh envelope belonging to the active query; retained
 * placeholder rows must never replace the URL of a different request.
 */
export function lastPageCorrection(
  page: Pick<
    FinderPageEnvelope,
    'total' | 'navigable_total' | 'navigation_limit' | 'page_size' | 'offset'
  >,
): number | null {
  if (page.total <= 0) return null;
  const limit = Math.min(page.navigation_limit, NAVIGATION_LIMIT);
  const total = Math.min(page.total, page.navigable_total, limit);
  if (total <= 0) return null;
  const crossesWindow = total === limit && page.offset + page.page_size > limit;
  if (page.offset < total && !crossesWindow) return null;
  const offset = lastValidOffset(total, page.page_size);
  return offset === page.offset ? null : offset;
}

/**
 * Enforce the cross-field invariants: Any never retains a tier floor, a
 * selected archetype defaults to B, page size is 1..50 (default 50), offset is
 * a non-negative integer below the ceiling (a URL at or beyond it canonicalizes
 * to the last valid page of the 10,000-row window before any request), and
 * weights are empty until slice 2.
 */
export function normalizeRankingState(
  input: Partial<FinderRankingState> | null | undefined,
): FinderRankingState {
  const archetype = isArchetypeKey(input?.archetype)
    ? input.archetype
    : ANY_ARCHETYPE;
  const minimumTier =
    archetype === ANY_ARCHETYPE
      ? null
      : (parseMinimumTier(input?.minimumTier) ?? DEFAULT_MINIMUM_TIER);
  const pageSize = isValidPageSize(input?.pageSize)
    ? input.pageSize
    : DEFAULT_PAGE_SIZE;
  const rawOffset = input?.offset;
  const offset =
    isInteger(rawOffset) && rawOffset >= 0
      ? rawOffset >= NAVIGATION_LIMIT
        ? lastValidOffset(NAVIGATION_LIMIT, pageSize)
        : rawOffset
      : 0;
  return {
    archetype,
    minimumTier,
    pageSize,
    offset,
    weights: WEIGHT_CONTRACT_DEFINED
      ? Object.freeze({ ...(input?.weights ?? {}) })
      : DEFAULT_RANKING_STATE.weights,
  };
}

// ---------------------------------------------------------------------------
// URL parsing
// ---------------------------------------------------------------------------

export type RankingUrlIssueReason =
  'unsupported' | 'duplicate' | 'invalid' | 'out-of-range' | 'ignored';

export type RankingUrlIssue = Readonly<{
  parameter: RankingUrlParameter;
  reason: RankingUrlIssueReason;
  value: string;
}>;

export type ParsedRankingUrl = Readonly<{
  state: FinderRankingState;
  /** Non-blocking "unsupported link option" notices for the status region. */
  issues: readonly RankingUrlIssue[];
  /** True when serializing `state` reproduces the given parameters exactly. */
  canonical: boolean;
}>;

const INTEGER_TEXT = /^(0|[1-9]\d*)$/;

function single(
  params: URLSearchParams,
  name: RankingUrlParameter,
  issues: RankingUrlIssue[],
): string | null {
  const values = params.getAll(name);
  if (values.length === 0) return null;
  if (values.length > 1) {
    issues.push({
      parameter: name,
      reason: 'duplicate',
      value: values.join(','),
    });
    return null;
  }
  return values[0];
}

function parseIntegerParameter(
  params: URLSearchParams,
  name: RankingUrlParameter,
  issues: RankingUrlIssue[],
): { value: number; raw: string } | null {
  const raw = single(params, name, issues);
  if (raw === null) return null;
  if (!INTEGER_TEXT.test(raw)) {
    issues.push({ parameter: name, reason: 'invalid', value: raw });
    return null;
  }
  const value = Number(raw);
  // A decimal literal too large for a safe integer is simply "too large":
  // the caller applies its own out-of-range rule (page size → default,
  // offset → last valid page of the navigation window).
  return {
    value: Number.isSafeInteger(value) ? value : Number.POSITIVE_INFINITY,
    raw,
  };
}

/**
 * Parse weight parameters (`<dimension>:<basis-points>`, integers 0–10000).
 * Until slice 2 defines the allowed dimensions this always yields `{}` and
 * reports each value as ignored.
 */
export function parseWeightParameters(
  values: readonly string[],
  options: { contractDefined: boolean } = {
    contractDefined: WEIGHT_CONTRACT_DEFINED,
  },
): { weights: Readonly<Record<string, number>>; issues: RankingUrlIssue[] } {
  const issues: RankingUrlIssue[] = [];
  const weights: Record<string, number> = {};
  for (const value of values) {
    if (!options.contractDefined) {
      issues.push({ parameter: 'weight', reason: 'ignored', value });
      continue;
    }
    const match = /^([a-z][a-z0-9_]*):(0|[1-9]\d{0,4})$/.exec(value);
    const basisPoints = match ? Number(match[2]) : Number.NaN;
    if (!match || basisPoints > WEIGHT_BASIS_POINTS_MAX) {
      issues.push({ parameter: 'weight', reason: 'invalid', value });
      continue;
    }
    if (match[1] in weights) {
      issues.push({ parameter: 'weight', reason: 'duplicate', value });
      continue;
    }
    weights[match[1]] = basisPoints;
  }
  return { weights: Object.freeze(weights), issues };
}

export function parseRankingSearchParams(
  params: URLSearchParams,
): ParsedRankingUrl {
  const issues: RankingUrlIssue[] = [];

  const archetypeRaw = single(params, 'archetype', issues);
  let archetype: FinderRankingState['archetype'] = ANY_ARCHETYPE;
  if (archetypeRaw !== null && archetypeRaw !== ANY_ARCHETYPE) {
    if (isArchetypeKey(archetypeRaw)) archetype = archetypeRaw;
    else
      issues.push({
        parameter: 'archetype',
        reason: 'unsupported',
        value: archetypeRaw,
      });
  }

  const tierRaw = single(params, 'min-tier', issues);
  let minimumTier: SelectedMinimumTier | null = null;
  if (tierRaw !== null) {
    if (archetype === ANY_ARCHETYPE) {
      issues.push({ parameter: 'min-tier', reason: 'ignored', value: tierRaw });
    } else {
      minimumTier = parseMinimumTier(tierRaw);
      if (minimumTier === null)
        issues.push({
          parameter: 'min-tier',
          reason: 'unsupported',
          value: tierRaw,
        });
    }
  }

  const pageSizeRaw = parseIntegerParameter(params, 'page_size', issues);
  let pageSize = DEFAULT_PAGE_SIZE;
  if (pageSizeRaw !== null) {
    if (isValidPageSize(pageSizeRaw.value)) pageSize = pageSizeRaw.value;
    else
      issues.push({
        parameter: 'page_size',
        reason: 'out-of-range',
        value: pageSizeRaw.raw,
      });
  }

  const offsetRaw = parseIntegerParameter(params, 'offset', issues);
  let offset = 0;
  if (offsetRaw !== null) {
    if (offsetRaw.value >= NAVIGATION_LIMIT) {
      issues.push({
        parameter: 'offset',
        reason: 'out-of-range',
        value: offsetRaw.raw,
      });
      offset = lastValidOffset(NAVIGATION_LIMIT, pageSize);
    } else {
      offset = offsetRaw.value;
    }
  }

  const weightResult = parseWeightParameters(params.getAll('weight'));
  issues.push(...weightResult.issues);

  const state = normalizeRankingState({
    archetype,
    minimumTier,
    pageSize,
    offset,
    weights: weightResult.weights,
  });
  const canonical =
    serializeRankingSearchParams(state, params).toString() ===
    params.toString();
  return { state, issues, canonical };
}

// ---------------------------------------------------------------------------
// URL serialization
// ---------------------------------------------------------------------------

function isRankingParameter(name: string): name is RankingUrlParameter {
  return (RANKING_URL_PARAMETERS as readonly string[]).includes(name);
}

/**
 * Serialize ranking state in canonical order (archetype, min-tier, page_size,
 * offset, sorted weights), then `selected`, then `system`, then every
 * unrelated parameter in its original order. Defaults are omitted so one
 * state always yields one URL.
 */
export function serializeRankingSearchParams(
  input: Partial<FinderRankingState>,
  base: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const state = normalizeRankingState(input);
  const output = new URLSearchParams();
  if (state.archetype !== ANY_ARCHETYPE) {
    output.set('archetype', state.archetype);
    if (state.minimumTier && state.minimumTier !== DEFAULT_MINIMUM_TIER)
      output.set('min-tier', state.minimumTier);
  }
  if (state.pageSize !== DEFAULT_PAGE_SIZE)
    output.set('page_size', String(state.pageSize));
  if (state.offset !== 0) output.set('offset', String(state.offset));
  if (WEIGHT_CONTRACT_DEFINED) {
    for (const dimension of Object.keys(state.weights).sort()) {
      output.append('weight', `${dimension}:${state.weights[dimension]}`);
    }
  }
  for (const passive of [SELECTION_URL_PARAMETER, DETAIL_URL_PARAMETER]) {
    for (const value of base.getAll(passive)) output.append(passive, value);
  }
  for (const [name, value] of base.entries()) {
    if (
      isRankingParameter(name) ||
      name === SELECTION_URL_PARAMETER ||
      name === DETAIL_URL_PARAMETER
    )
      continue;
    output.append(name, value);
  }
  return output;
}

export function hasRankingSearchParams(params: URLSearchParams): boolean {
  return RANKING_URL_PARAMETERS.some((name) => params.has(name));
}

/**
 * Disabled-build canonical form: every F4 parameter removed, `selected`,
 * `system` and unrelated parameters preserved in their original order.
 */
export function stripRankingSearchParams(
  params: URLSearchParams,
): URLSearchParams {
  const output = new URLSearchParams();
  for (const [name, value] of params.entries()) {
    if (!isRankingParameter(name)) output.append(name, value);
  }
  return output;
}

// ---------------------------------------------------------------------------
// Request selection
// ---------------------------------------------------------------------------

export type AnchorCoordinates = Readonly<{
  x: number | null | undefined;
  y: number | null | undefined;
  z: number | null | undefined;
}>;

export type FinderRequest =
  | Readonly<{
      kind: 'any';
      request: ExploreSearchRequest;
      /**
       * Facade/URL page size. Near the ceiling the wire `size` is clamped
       * below it; the envelope and Previous/Next keep this value.
       */
      pageSize: number;
    }>
  | Readonly<{ kind: 'rankings'; request: ArchetypeRankingsRequest }>;

const finiteCoordinate = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

/**
 * An anchor contributes reference coordinates only when all three are finite.
 * `NaN`/`Infinity` (for example from a malformed restored anchor) would
 * serialize to `null` and fail request validation, so they fall back to the
 * galaxy-wide request instead.
 */
const anchoredCoordinates = (
  anchor: AnchorCoordinates | null | undefined,
): { x: number; y: number; z: number } | null =>
  anchor &&
  finiteCoordinate(anchor.x) &&
  finiteCoordinate(anchor.y) &&
  finiteCoordinate(anchor.z)
    ? { x: anchor.x, y: anchor.y, z: anchor.z }
    : null;

/** Wire window for one page: `limit` never crosses the navigation ceiling. */
export function rankingWireWindow(
  state: Pick<FinderRankingState, 'pageSize' | 'offset'>,
): { limit: number; offset: number } {
  const { pageSize, offset } = normalizeRankingState(state);
  return { limit: Math.min(pageSize, NAVIGATION_LIMIT - offset), offset };
}

/**
 * Explore request derivation for the default-off ranking path:
 * anchored → distance-sorted bounded-radius search, otherwise a 24-row galaxy-wide
 * development ranking. It carries no F4 field and no tier floor.
 */
export function legacyExploreRequest(
  anchor: AnchorCoordinates | null | undefined,
  radius: SearchRadius = DEFAULT_SEARCH_RADIUS,
): ExploreSearchRequest {
  const coords = anchoredCoordinates(anchor);
  if (coords) {
    return {
      reference_coords: coords,
      filters: {
        distance: { min: 0, max: normalizeSearchRadius(radius) },
        economy: 'any',
      },
      sort_by: 'distance',
      size: LEGACY_EXPLORE_PAGE_SIZE,
      from: 0,
    };
  }
  return {
    galaxy_wide: true,
    sort_by: 'development',
    size: LEGACY_EXPLORE_PAGE_SIZE,
    from: 0,
  };
}

/**
 * Choose the request for a ranking state. With the gate disabled the rankings
 * branch is unreachable: every state, including one hydrated from a
 * hand-authored archetype URL, yields today's local-search request.
 */
export function selectFinderRequest(
  input: Partial<FinderRankingState> | null | undefined,
  anchor: AnchorCoordinates | null | undefined,
  options: { enabled: boolean; radius?: SearchRadius },
): FinderRequest {
  if (!options.enabled) {
    return {
      kind: 'any',
      request: legacyExploreRequest(anchor, options.radius),
      pageSize: LEGACY_EXPLORE_PAGE_SIZE,
    };
  }
  const state = normalizeRankingState(input);
  const window = rankingWireWindow(state);
  if (state.archetype === ANY_ARCHETYPE) {
    const coords = anchoredCoordinates(anchor);
    const request: ExploreSearchRequest = coords
      ? {
          reference_coords: coords,
          filters: {
            distance: { min: 0, max: normalizeSearchRadius(options.radius) },
            economy: 'any',
          },
          sort_by: 'distance',
          size: window.limit,
          from: window.offset,
        }
      : {
          galaxy_wide: true,
          sort_by: 'development',
          size: window.limit,
          from: window.offset,
        };
    return { kind: 'any', request, pageSize: state.pageSize };
  }
  return {
    kind: 'rankings',
    request: {
      archetype: state.archetype,
      min_score: minimumScoreForTier(state.minimumTier ?? DEFAULT_MINIMUM_TIER),
      page_size: state.pageSize,
      offset: state.offset,
    },
  };
}
