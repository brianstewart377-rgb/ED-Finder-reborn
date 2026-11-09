/** Shareable near-system search inputs, independent of the F4 ranking flag. */
import { parseId64, type Id64 } from '$lib/domain/id64';
import {
  BODY_FILTER_OPTIONS,
  isBodyFilterKey,
  normalizeBodyFilters,
  type BodyFilterValues,
} from './body-filters';

export const NEAR_URL_PARAMETER = 'near';
export const RADIUS_URL_PARAMETER = 'radius';
export const BODIES_URL_PARAMETER = 'bodies';
export const SEARCH_RADIUS_OPTIONS = [100, 250, 500, 1000, 2000] as const;
export type SearchRadius = (typeof SEARCH_RADIUS_OPTIONS)[number];
export const DEFAULT_SEARCH_RADIUS: SearchRadius = 500;
/** Enough for every supported key with both three-digit bounds. */
export const MAX_BODIES_PARAMETER_LENGTH = 640;

export type ExploreSearchState = Readonly<{
  near: Id64 | null;
  radius: SearchRadius;
  bodyFilters: BodyFilterValues;
}>;

export type ParsedExploreSearch = ExploreSearchState &
  Readonly<{
    issues: readonly string[];
    canonical: URLSearchParams;
  }>;

function boundedNear(value: unknown): Id64 | null {
  if (typeof value !== 'string' || value.length > 20) return null;
  try {
    return parseId64(value);
  } catch {
    return null;
  }
}

/**
 * The API caps an anchored search that carries body filters at 500 LY
 * (`LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY`); only the unfiltered
 * nearest-first path may use the wider radii. The URL keeps the chosen radius
 * so it resumes when the filters are cleared.
 */
export const FILTERED_SEARCH_RADIUS_CAP_LY: SearchRadius = 500;

export function effectiveSearchRadius(
  radius: SearchRadius,
  bodyFilters: BodyFilterValues,
): SearchRadius {
  return Object.keys(bodyFilters).length
    ? (Math.min(radius, FILTERED_SEARCH_RADIUS_CAP_LY) as SearchRadius)
    : radius;
}

export function normalizeSearchRadius(value: unknown): SearchRadius {
  return (
    SEARCH_RADIUS_OPTIONS.find((radius) => radius === value) ??
    DEFAULT_SEARCH_RADIUS
  );
}

/** Canonical order is the control order; unrelated parameters retain their order. */
export function serializeExploreSearchParams(
  input: Partial<ExploreSearchState>,
  base: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const output = new URLSearchParams(base);
  output.delete(NEAR_URL_PARAMETER);
  output.delete(RADIUS_URL_PARAMETER);
  output.delete(BODIES_URL_PARAMETER);
  const near = boundedNear(input.near);
  if (near !== null) output.set(NEAR_URL_PARAMETER, near);
  const radius = normalizeSearchRadius(input.radius);
  if (radius !== DEFAULT_SEARCH_RADIUS)
    output.set(RADIUS_URL_PARAMETER, String(radius));
  const filters = normalizeBodyFilters(input.bodyFilters);
  const bodies = BODY_FILTER_OPTIONS.flatMap(({ key }) => {
    const range = filters[key];
    return range ? [`${key}:${range.min ?? ''}-${range.max ?? ''}`] : [];
  }).join(',');
  if (bodies) output.set(BODIES_URL_PARAMETER, bodies);
  return output;
}

export function parseExploreSearchParams(
  params: URLSearchParams,
): ParsedExploreSearch {
  const issues: string[] = [];
  const nearValues = params.getAll(NEAR_URL_PARAMETER);
  const near = nearValues.length === 1 ? boundedNear(nearValues[0]) : null;
  if (nearValues.length && near === null)
    issues.push('The search-near link was invalid and has been cleared.');

  const radiusValues = params.getAll(RADIUS_URL_PARAMETER);
  const parsedRadius =
    radiusValues.length === 1
      ? SEARCH_RADIUS_OPTIONS.find(
          (radius) => String(radius) === radiusValues[0],
        )
      : undefined;
  const radius = parsedRadius ?? DEFAULT_SEARCH_RADIUS;
  if (radiusValues.length && parsedRadius === undefined)
    issues.push('Invalid search radius in this link has been cleared.');

  let bodyFilters: BodyFilterValues = {};
  const bodyValues = params.getAll(BODIES_URL_PARAMETER);
  let invalidBodies = bodyValues.length > 1;
  if (bodyValues.length === 1) {
    const raw = bodyValues[0];
    if (!raw || raw.length > MAX_BODIES_PARAMETER_LENGTH) {
      invalidBodies = true;
    } else {
      const seen = new Set<string>();
      for (const entry of raw.split(',')) {
        // Detect duplicates even when one occurrence has a malformed range.
        const key = entry.split(':', 1)[0];
        if (!isBodyFilterKey(key)) {
          invalidBodies = true;
          continue;
        }
        if (seen.has(key)) {
          delete bodyFilters[key];
          invalidBodies = true;
          continue;
        }
        seen.add(key);
        const match =
          /^[a-z_]+:((?:0|[1-9]\d{0,2})?)-((?:0|[1-9]\d{0,2})?)$/.exec(entry);
        if (!match || (!match[1] && !match[2])) {
          invalidBodies = true;
          continue;
        }
        const range = {
          ...(match[1] ? { min: Number(match[1]) } : {}),
          ...(match[2] ? { max: Number(match[2]) } : {}),
        };
        const valid = normalizeBodyFilters({ [key]: range });
        if (!valid[key]) invalidBodies = true;
        else bodyFilters[key] = valid[key];
      }
      bodyFilters = normalizeBodyFilters(bodyFilters);
    }
  }
  if (invalidBodies)
    issues.push('Invalid body filters in this link have been cleared.');
  const state = { near, radius, bodyFilters };
  return {
    ...state,
    issues,
    canonical: serializeExploreSearchParams(state, params),
  };
}
