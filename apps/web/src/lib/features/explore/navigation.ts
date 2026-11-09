import { goto } from '$app/navigation';
import { resolve } from '$app/paths';
import { page } from '$app/state';
import { isId64, type Id64 } from '$lib/domain/id64';

/** Resolved history cache for the shareable `near=<id64>` search anchor. */
export type ExploreAnchor = Readonly<{
  id64: Id64;
  name: string;
  x: number;
  y: number;
  z: number;
}>;

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace -- SvelteKit's PageState extension point
  namespace App {
    interface PageState {
      anchor?: ExploreAnchor | null;
      /** Explicit Finder URL intent retained when default parameters are removed. */
      finderRequested?: boolean;
    }
  }
}

/** Fail closed when restored history cannot describe an anchored search. */
export function readExploreAnchor(state: unknown): ExploreAnchor | null {
  if (typeof state !== 'object' || state === null || !('anchor' in state)) {
    return null;
  }
  const anchor = state.anchor;
  if (typeof anchor !== 'object' || anchor === null) return null;
  if (
    !('id64' in anchor) ||
    !isId64(anchor.id64) ||
    !('name' in anchor) ||
    typeof anchor.name !== 'string' ||
    anchor.name.trim().length === 0
  ) {
    return null;
  }
  if (
    !('x' in anchor) ||
    !('y' in anchor) ||
    !('z' in anchor) ||
    typeof anchor.x !== 'number' ||
    typeof anchor.y !== 'number' ||
    typeof anchor.z !== 'number' ||
    !Number.isFinite(anchor.x) ||
    !Number.isFinite(anchor.y) ||
    !Number.isFinite(anchor.z)
  ) {
    return null;
  }
  return {
    id64: anchor.id64,
    name: anchor.name,
    x: anchor.x,
    y: anchor.y,
    z: anchor.z,
  };
}

/**
 * Every F4 navigation that retains Explore context passes through this helper.
 * Read the current state at dispatch, so an earlier render cannot restore a
 * stale anchor. Explicit state changes (anchor commit/clear) retain other keys.
 * Destinations are already resolved paths or URLs, including reactive page.url.
 */
export function navigateWithAnchor(
  destination: string | URL,
  options: { replaceState?: boolean; state?: App.PageState } = {},
): Promise<void> {
  // eslint-disable-next-line svelte/no-navigation-without-resolve -- callers pass resolved paths or the current page URL
  return goto(destination, {
    replaceState: options.replaceState ?? false,
    state:
      options.state === undefined
        ? page.state
        : { ...page.state, ...options.state },
    keepFocus: true,
    noScroll: true,
  });
}

/** Preserve the originating Explore query for Inspect's return navigation. */
export function inspectHref(id64: Id64, source: URL): string {
  const params = new URLSearchParams({
    system: id64,
    from: source.searchParams.toString(),
  });
  return `${resolve('/inspect')}?${params}`;
}
