import { parseId64, type Id64 } from '$lib/domain/id64';

export interface LegacyDestination {
  pathname: '/explore' | '/inspect' | '/plan' | '/review';
  search: string;
}

function id(value: string | undefined): Id64 | null {
  if (!value) return null;
  try {
    return parseId64(value);
  } catch {
    return null;
  }
}

function withQuery(
  pathname: LegacyDestination['pathname'],
  currentSearch: string,
  entries: ReadonlyArray<readonly [string, string | null]>,
): LegacyDestination {
  const query = new URLSearchParams(currentSearch);
  for (const [key, value] of entries) {
    if (value !== null) query.set(key, value);
  }
  const encoded = query.toString();
  return { pathname, search: encoded ? `?${encoded}` : '' };
}

/**
 * Map the retired React hash vocabulary into the four canonical V3 journey
 * routes. Unknown legacy surfaces return to Explore; they never establish a
 * second route model.
 */
export function legacyHashDestination(
  hash: string,
  currentSearch = '',
): LegacyDestination | null {
  if (!hash) return null;
  const parts = hash.replace(/^#\/?/u, '').split('/').filter(Boolean);
  const host = parts[0] ?? 'finder';

  if (
    host === 'system' ||
    (parts[1] === 'system' && host !== 'colony-planner')
  ) {
    const system = id(host === 'system' ? parts[1] : parts[2]);
    return withQuery('/inspect', currentSearch, [['system', system]]);
  }

  if (host === 'colony-planner') {
    const system = parts[1] === 'system' ? id(parts[2]) : null;
    const entries: Array<readonly [string, string | null]> = [
      ['system', system],
    ];
    let cursor = system ? 3 : 1;
    if (parts[cursor] === 'project' && parts[cursor + 1]) {
      try {
        entries.push(['project', decodeURIComponent(parts[cursor + 1])]);
      } catch {
        return withQuery('/plan', currentSearch, []);
      }
      cursor += 2;
    }
    if (parts[cursor] === 'mode' && parts[cursor + 1]) {
      entries.push(['mode', parts[cursor + 1]]);
      cursor += 2;
    }
    if (parts[cursor] === 'detail') {
      entries.push(['detail', id(parts[cursor + 1])]);
    }
    return withQuery('/plan', currentSearch, entries);
  }

  if (host === 'map' || host === 'finder' || host === 'search-tuning')
    return withQuery('/explore', currentSearch, []);
  if (host === 'fc')
    return withQuery('/plan', currentSearch, [['view', 'fleet-carrier']]);
  return withQuery('/review', currentSearch, [['legacy', host]]);
}

export function applyLegacyHash(
  location: Location,
  replace: (url: string) => void,
): boolean {
  const destination = legacyHashDestination(location.hash, location.search);
  if (!destination) return false;
  replace(`${destination.pathname}${destination.search}`);
  return true;
}
