import type { AutocompleteSystem } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import {
  ANY_ARCHETYPE,
  isArchetypeKey,
} from '$lib/features/explore/archetypes';
import { serializeRankingSearchParams } from '$lib/features/explore/ranking-state';
import { serializeExploreSearchParams } from '$lib/features/explore/search-state';

/** Build a shareable Finder URL only from a chosen catalogue suggestion. */
export function buildHomeSearchUrl(
  selected: Pick<AutocompleteSystem, 'id64'> | null | undefined,
  archetype: string = ANY_ARCHETYPE,
  finderEnabled: boolean = false,
): `/explore?${string}` | null {
  const id64 = selected?.id64;
  if (typeof id64 !== 'string' || id64.length > 20) return null;
  if (
    finderEnabled &&
    archetype !== ANY_ARCHETYPE &&
    !isArchetypeKey(archetype)
  )
    return null;

  let near;
  try {
    near = parseId64(id64);
  } catch {
    return null;
  }
  const search = serializeExploreSearchParams({ near });
  const params = serializeRankingSearchParams(
    {
      archetype:
        finderEnabled && isArchetypeKey(archetype) ? archetype : ANY_ARCHETYPE,
    },
    search,
  );
  return `/explore?${params.toString()}`;
}
