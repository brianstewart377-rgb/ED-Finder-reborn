// Which system is selected on the Explore page, and what the map shows of
// it. The selected system may be on the page of results, kept from an
// earlier page, looked up by its id, or be the star searched near. Where its
// position is known, the map flies there and shows the real stars near it.
//
// The page creates this once and reads what it needs through it. The page's
// own values are passed in as functions so that they stay live.
import {
  type ExploreSystem,
  getCatalogueViewportSystems,
  getSystem,
} from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import { FINDER_F4_ENABLED } from '$lib/config';
import type { Id64 } from '$lib/domain/id64';
import {
  boxAround,
  chosenFromFind,
  type Showing,
  starClassInBox,
  starsInRange,
} from '$lib/galaxy-map/map-content';
import { stellarPresentation } from '$lib/spatial/stellar-presentation';
import { createQuery } from '@tanstack/svelte-query';
import { archetypeLabel } from './archetypes';
import type { ExploreViewState } from './explore-view-state.svelte.ts';
import type { ExploreAnchor } from './navigation';

export function createSelectedSystem(source: {
  viewState: () => ExploreViewState;
  /** The systems on the page of results on screen. */
  systems: () => readonly ExploreSystem[];
  /** The request for that page: whether its first answer is still awaited. */
  results: { readonly isPending: boolean };
  /** The star searched near, if there is one. */
  anchor: () => ExploreAnchor | null;
  /** Whether the galaxy map has started, is starting, or could not start. */
  mapPhase: () => 'starting' | 'ready' | 'failed';
  /** The selection kept by the site itself, used when Find's own is switched off. */
  selectedValue: () => Id64 | null;
}) {
  const { results, selectedValue } = source;
  const viewState = $derived(source.viewState());
  const systems = $derived(source.systems());
  const anchor = $derived(source.anchor());
  const mapPhase = $derived(source.mapPhase());

  const selectedId = $derived(
    FINDER_F4_ENABLED ? viewState.urlSelection : selectedValue(),
  );
  // A system was just chosen and the address naming it is on its way: what
  // the address says now is the old selection, so nothing is looked up or
  // kept for it meanwhile.
  const settling = $derived(
    viewState.pendingSelection !== null &&
      viewState.pendingSelection !== viewState.urlSelection,
  );
  const selectedOnPage = $derived(
    systems.find((system) => system.id64 === selectedId),
  );
  // F4 URL selection uses result pages or exact-system resolution, independent
  // of catalogue viewport response timing.
  const knownSelectedSystem = $derived(
    selectedOnPage ??
      (viewState.retainedSelectedSystem?.id64 === selectedId
        ? viewState.retainedSelectedSystem
        : null),
  );
  const needsSelectionResolution = $derived(
    FINDER_F4_ENABLED &&
      !settling &&
      viewState.rankingUrlReady &&
      viewState.urlSelection !== null &&
      !selectedOnPage &&
      (viewState.retainedSelectedSystem?.id64 !== viewState.urlSelection ||
        !chosenFromFind(viewState.retainedSelectedSystem)) &&
      (!viewState.hasResultsIntent || !results.isPending),
  );
  const selectedDetail = FINDER_F4_ENABLED
    ? createQuery(() => {
        const id64 = viewState.urlSelection;
        return {
          queryKey: [...queryKeys.all, 'explore', 'selected-point', id64],
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            if (!id64) throw new Error('No selected system');
            return getSystem(id64, signal);
          },
          enabled: needsSelectionResolution,
          retry: 0,
        };
      })
    : undefined;
  const selectedDetailData = $derived(selectedDetail?.data);
  const reconstructedSelection = $derived<ExploreSystem | null>(
    FINDER_F4_ENABLED &&
      selectedDetailData?.id64 === viewState.urlSelection &&
      selectedDetailData
      ? {
          id64: selectedDetailData.id64,
          name: selectedDetailData.name,
          coords: {
            x: selectedDetailData.x,
            y: selectedDetailData.y,
            z: selectedDetailData.z,
          },
        }
      : null,
  );
  // The star searched near, when it is the selected system. Its position
  // came with the suggestion that was chosen, so it can be shown at once.
  const anchorAsSelected = $derived<ExploreSystem | null>(
    anchor && anchor.id64 === selectedId
      ? {
          id64: anchor.id64,
          name: anchor.name,
          coords: { x: anchor.x, y: anchor.y, z: anchor.z },
        }
      : null,
  );
  const selectedMapSystem = $derived.by((): ExploreSystem | null => {
    if (FINDER_F4_ENABLED)
      return (
        knownSelectedSystem ??
        reconstructedSelection ??
        anchorAsSelected ??
        (viewState.urlSelection ? { id64: viewState.urlSelection } : null)
      );
    const finder = systems.find((system) => system.id64 === selectedValue());
    if (finder)
      return {
        id64: finder.id64,
        name: finder.name,
        coords: finder.coords,
        main_star_type: finder.main_star_type,
        main_star_subtype: finder.main_star_subtype,
        distance: finder.distance,
        population: finder.population,
        primaryEconomy: finder.primaryEconomy,
        allegiance: finder.allegiance,
        security: finder.security,
      };
    if (viewState.retainedSelectedSystem?.id64 === selectedId)
      return viewState.retainedSelectedSystem;
    // The star searched near is selected when it is chosen.
    return anchorAsSelected;
  });
  const selectedMapName = $derived(
    selectedMapSystem
      ? (selectedMapSystem.name ?? `System ${selectedMapSystem.id64}`)
      : null,
  );
  // The map shows the selected system: it flies there and shows the real
  // stars within 15 ly of it. A system whose position is not known cannot be
  // shown.
  const chosenSystem = $derived(chosenFromFind(selectedMapSystem));
  // The selected system's position is still being asked for: from the page
  // of results it may be on, or by its id. Until that is settled, the page
  // does not say the position is unavailable.
  const selectionBeingLookedUp = $derived(
    FINDER_F4_ENABLED &&
      viewState.urlSelection !== null &&
      !chosenSystem &&
      ((viewState.hasResultsIntent && results.isPending) ||
        (needsSelectionResolution &&
          (selectedDetail?.isPending ?? false) &&
          !(selectedDetail?.isError ?? false))),
  );
  const selectionCannotBeShown = $derived(
    selectedMapSystem !== null && !chosenSystem && !selectionBeingLookedUp,
  );
  const starsNearChosen = createQuery(() => {
    const chosen = chosenSystem;
    const box = chosen ? boxAround(chosen) : null;
    return {
      queryKey: [
        ...queryKeys.all,
        'map',
        'stars-in-range',
        chosen?.id64 ?? null,
        JSON.stringify(box),
      ],
      queryFn: ({ signal }: { signal: AbortSignal }) => {
        if (!box) throw new Error('No system is chosen');
        return getCatalogueViewportSystems(box, signal);
      },
      // A map that could not start has nowhere to show them.
      enabled: box !== null && mapPhase !== 'failed',
      staleTime: 5 * 60_000,
      retry: 1,
    };
  });
  const showing = $derived.by((): Showing | null => {
    const chosen = chosenSystem;
    if (!chosen) return null;
    const facts = {
      potential: selectedMapSystem?.overall_development_potential,
      primary: archetypeLabel(selectedMapSystem?.primary_archetype),
      secondary: archetypeLabel(selectedMapSystem?.secondary_archetype),
      claim: selectedMapSystem?.claim_status,
    };
    const answer = starsNearChosen.data;
    if (!answer)
      return {
        chosen,
        facts,
        stars: [],
        starsState: starsNearChosen.isError ? 'failed' : 'loading',
        starsCutShort: false,
      };
    return {
      chosen: {
        ...chosen,
        star: starClassInBox(answer.systems, chosen.id64) ?? chosen.star,
      },
      facts,
      stars: starsInRange(answer.systems, chosen),
      starsState: 'ready',
      starsCutShort: answer.truncated,
    };
  });
  $effect(() => {
    if (!FINDER_F4_ENABLED && selectedMapSystem?.coords)
      viewState.retainedSelectedSystem = selectedMapSystem;
  });
  $effect(() => {
    if (!FINDER_F4_ENABLED || settling) return;
    // Retain a selected result or exact-system resolution across page changes.
    // Resolution is keyed by id, so late responses cannot move the selection.
    const selected = selectedOnPage ?? reconstructedSelection;
    if (
      selected &&
      (chosenFromFind(selected) ||
        viewState.retainedSelectedSystem?.id64 !== selected.id64)
    )
      viewState.retainedSelectedSystem = selected;
    // The star searched near is kept too, where nothing fuller is held of
    // it: its position came with its suggestion, so it is never looked up
    // by its id, and it stays shown when the star is searched near no more.
    else if (
      anchorAsSelected &&
      viewState.retainedSelectedSystem?.id64 !== anchorAsSelected.id64
    )
      viewState.retainedSelectedSystem = anchorAsSelected;
    else if (viewState.retainedSelectedSystem?.id64 !== viewState.urlSelection)
      viewState.retainedSelectedSystem = null;
  });
  const selectedStarPresentation = $derived(
    stellarPresentation({
      type: selectedMapSystem?.main_star_type,
      subtype: selectedMapSystem?.main_star_subtype,
    }),
  );

  return {
    /** The id of the selected system, if one is selected. */
    get selectedId() {
      return selectedId;
    },
    /** The selected system, as much of it as is known. */
    get selectedMapSystem() {
      return selectedMapSystem;
    },
    get selectedMapName() {
      return selectedMapName;
    },
    /** The selected system, when its position is known and the map can show it. */
    get chosenSystem() {
      return chosenSystem;
    },
    get selectionBeingLookedUp() {
      return selectionBeingLookedUp;
    },
    get selectionCannotBeShown() {
      return selectionCannotBeShown;
    },
    /** The request for the real stars near the selected system. */
    starsNearChosen,
    /** What the map is to show: the selected system, facts about it, and those stars. */
    get showing() {
      return showing;
    },
    get selectedStarPresentation() {
      return selectedStarPresentation;
    },
  };
}
