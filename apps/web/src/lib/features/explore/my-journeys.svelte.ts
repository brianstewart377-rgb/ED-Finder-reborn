// "My journeys" on the Explore page's galaxy map: the systems the signed-in
// account has visited, from its imported journals, and what the page says
// about them while they load, fail or are switched off.
//
// Only the account itself ever sees them. Everything here is keyed by the
// account, so a sign-out or a switch of account never shows another's travel.
import { createQuery } from '@tanstack/svelte-query';
import { fromStore } from 'svelte/store';
import {
  ApiError,
  getCommanderJournalSummary,
  getCommanderTravelHeatmap,
} from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import { auth } from '$lib/auth/auth';
import {
  journeyLines,
  JOURNEYS_ASKED_FOR,
  type Journey,
} from '$lib/galaxy-map/map-content';

/** Where "My journeys" stands, as the status line beside the map says it. */
export type MyJourneysState =
  | 'checking'
  | 'auth-error'
  | 'summary-error'
  | 'signed-out'
  | 'off'
  | 'no-journals'
  | 'loading'
  | 'error'
  | 'empty'
  | 'ready';

/**
 * "My journeys" for the Explore page. `shown` says whether the visitor has
 * the layer switched on; nothing is asked of the API while it is off.
 */
export function createMyJourneys(shown: () => boolean) {
  const session = fromStore(auth);
  // The travel heatmap is the signed-in account's own V3 journal travel; the
  // cache key carries the account so a sign-out/switch never reuses rows.
  const travelAccountId = $derived(
    session.current.loading ? null : (session.current.user?.account_id ?? null),
  );

  // "My journeys": every system the signed-in account has visited, from its
  // imported journals, asked for once as one marker for each system. When
  // there are more than the API returns at once, it returns the most
  // recently visited.
  const JOURNEYS_REQUEST = {
    minX: -100_000,
    maxX: 100_000,
    minY: -100_000,
    maxY: 100_000,
    minZ: -100_000,
    maxZ: 100_000,
    limit: JOURNEYS_ASKED_FOR,
  } as const;
  // At this zoom the API answers with one marker for each system, not cells.
  const JOURNEYS_ZOOM = 1;
  const commanderVisits = createQuery(() => ({
    queryKey: queryKeys.commanderTravel(
      travelAccountId ?? '',
      JOURNEYS_REQUEST,
    ),
    queryFn: ({ signal }) =>
      getCommanderTravelHeatmap(JOURNEYS_REQUEST, JOURNEYS_ZOOM, signal),
    enabled: shown() && travelAccountId !== null,
    staleTime: 5 * 60_000,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
  }));
  const journalSummary = createQuery(() => ({
    queryKey: [...queryKeys.auth(), travelAccountId, 'journal-summary'],
    queryFn: ({ signal }) => getCommanderJournalSummary(signal),
    enabled: shown() && travelAccountId !== null,
    staleTime: 20_000,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
  }));
  const travelHeatmapState = $derived.by((): MyJourneysState => {
    if (session.current.loading) return 'checking';
    // A failed session check is not "signed out": the visitor may well be
    // signed in, so offer a retry rather than a sign-in prompt.
    if (travelAccountId === null)
      return session.current.error ? 'auth-error' : 'signed-out';
    if (!shown()) return 'off';
    if (
      journalSummary.isError &&
      journalSummary.error instanceof ApiError &&
      journalSummary.error.status === 401
    )
      return 'signed-out';
    if (commanderVisits.isError)
      return commanderVisits.error instanceof ApiError &&
        commanderVisits.error.status === 401
        ? 'signed-out'
        : 'error';
    if (!commanderVisits.data?.count && journalSummary.isError)
      return 'summary-error';
    if (
      !commanderVisits.data?.count &&
      !journalSummary.isError &&
      journalSummary.data?.imported_files === 0
    )
      return 'no-journals';
    if (!commanderVisits.data) return 'loading';
    return commanderVisits.data.count === 0 ? 'empty' : 'ready';
  });
  const journeys = $derived.by((): Journey[] | null => {
    // Never draw cached private travel once the query is failing (an expired
    // session keeps the last data alongside a 401).
    if (
      !shown() ||
      travelAccountId === null ||
      travelHeatmapState === 'signed-out' ||
      commanderVisits.isError ||
      !commanderVisits.data
    )
      return null;
    return commanderVisits.data.visits.flatMap((visit) =>
      visit.kind === 'marker' && visit.systemId64
        ? [
            {
              id64: visit.systemId64,
              name: visit.systemName?.trim() || `System ${visit.systemId64}`,
              x: visit.x,
              y: visit.y,
              z: visit.z,
              visits: visit.visitCount,
              firstVisit: visit.firstVisitedAt,
              lastVisit: visit.lastVisitedAt,
            },
          ]
        : [],
    );
  });
  // The map draws only the longer trips as lines: the page says so wherever
  // there is such a line, so that the lines are not taken for all the travel.
  const journeysHaveLines = $derived(
    journeys !== null && journeyLines(journeys).length > 0,
  );

  return {
    /** Where it stands: what the status line says. */
    get state(): MyJourneysState {
      return travelHeatmapState;
    },
    /** The systems to draw, or null when none may be drawn. */
    get journeys(): Journey[] | null {
      return journeys;
    },
    /** Whether any trip is long enough to be drawn as a line. */
    get haveLines(): boolean {
      return journeysHaveLines;
    },
    /** How many visited systems the API returned. */
    get count(): number {
      return commanderVisits.data?.count ?? 0;
    },
    /** Whether there were more than the API returns at once. */
    get truncated(): boolean {
      return commanderVisits.data?.truncated ?? false;
    },
    retryVisits: () => commanderVisits.refetch(),
    retrySummary: () => journalSummary.refetch(),
  };
}
