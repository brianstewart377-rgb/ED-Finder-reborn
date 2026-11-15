<script lang="ts" module>
  import type { MapView } from '@elite-dangerous-almanac/galaxy-map';

  /** Where the map was when it was last left, to open there again. */
  export type MapResume = Readonly<{
    view: MapView;
    /** The whole-galaxy view the map first opened on. */
    home: MapView;
    /** The id of the system it was showing, or null. */
    shownId: string | null;
  }>;
</script>

<script lang="ts">
  /**
   * The galaxy map: Almanac's Galaxy-Map, exactly as published, given our own
   * data (`./map-content`). This file only hands that data to the map and
   * moves its camera; every rule about what is shown is in `map-content.ts`.
   *
   * The map needs WebGL2 on a real graphics card. Where it cannot start, the
   * page says so in plain words and the rest of the site works without it.
   */
  import { onMount, untrack } from 'svelte';
  import { asset } from '$app/paths';
  import type {
    GalaxyMap,
    RealSystem,
    SystemDetails,
  } from '@elite-dangerous-almanac/galaxy-map';
  import {
    detailsFor,
    mapContent,
    OPENING_DISTANCE_LY,
    OPENING_PITCH_DEG,
    whatToAdd,
    type Journey,
    type MapContent,
    type MapSystem,
    type Showing,
  } from './map-content';

  let {
    showing,
    journeys = null,
    resume = null,
    onScreen = true,
    onLeave,
    onShowOnMap,
    onInspect,
    phase = $bindable('starting'),
  }: {
    /** The system chosen in Find and the stars in range of it, or null. */
    showing: Showing | null;
    /** The systems the player has visited, or null when they are not shown. */
    journeys?: readonly Journey[] | null;
    resume?: MapResume | null;
    /**
     * Whether the map can be seen. While it cannot (a phone showing the
     * Finder in its place), it is given nothing new and so draws nothing;
     * it catches up, and flies, when it is seen again. A browser draws a
     * hidden map's frames without pacing them, so a flight nobody sees could
     * pile up work for a slow graphics card.
     */
    onScreen?: boolean;
    onLeave?: (resume: MapResume) => void;
    /** A star or visited system was asked to be shown in its own right. */
    onShowOnMap?: (system: MapSystem) => void;
    onInspect?: (id64: string) => void;
    phase?: 'starting' | 'ready' | 'failed';
  } = $props();

  const uid = $props.id();
  let stage = $state<HTMLDivElement>();
  let canvas = $state<HTMLCanvasElement>();
  let problem = $state('');
  // The map itself is not page state: nothing is drawn from it by Svelte.
  let map: GalaxyMap | null = null;
  let home: MapView | null = null;
  /** What the map was last given: the panel reads it when a system is opened. */
  let given: {
    showing: Showing | null;
    journeys: readonly Journey[] | null;
  } = { showing: null, journeys: null };
  /** What is on the map now. */
  let drawn: MapContent | null = null;
  /**
   * The map's panel is open on the chosen system and its stars are still on
   * their way: the panel shows the map's own "loading" until they are in.
   */
  let waiting: {
    system: RealSystem;
    settle: (details: SystemDetails | null) => void;
  } | null = null;
  /** The id of the chosen system the map last flew to. */
  let shownId: string | null = null;
  /** The chosen system a flight of ours is still on its way to. */
  let flyingTo: string | null = null;
  let flight = 0;
  /**
   * The system whose open panel says something older than what is now known
   * of it, because that came in while the map was flying there. The map
   * reads a panel once, when it opens, and opening one takes the camera: so
   * the flight is left to finish, and the panel is opened again on landing.
   */
  let wordsOwed: string | null = null;

  /**
   * Open the map's panel again on the system it is open on, so that it asks
   * what to say once more, and put the camera back where it was.
   */
  function readAgain(held: GalaxyMap, selected: string): void {
    const viewBefore = held.getView();
    held.setSelection(null);
    held.setSelection(selected);
    held.setView(viewBefore);
  }

  function fly(chosen: MapSystem): void {
    if (!map) return;
    const mine = ++flight;
    flyingTo = chosen.id64;
    void map
      .flyTo({
        cursor: [chosen.x, chosen.y, chosen.z],
        distance: OPENING_DISTANCE_LY,
        pitch: OPENING_PITCH_DEG,
      })
      .then(() => {
        // Landed, or the player took the camera. A newer flight of ours
        // ending this one is neither.
        if (mine !== flight) return;
        flyingTo = null;
        const owed = wordsOwed;
        wordsOwed = null;
        if (map && owed !== null && map.getSelection()?.id64 === owed)
          readAgain(map, owed);
      });
  }

  /** Fly back to the chosen system, as "Centre on …" asks. */
  export function centre(): void {
    const chosen = given.showing?.chosen;
    // A flight of ours already on its way there is left to finish.
    if (map && chosen && flyingTo !== chosen.id64) fly(chosen);
  }

  /** Fly out to the whole galaxy. */
  export function wholeGalaxy(): void {
    if (!map || !home) return;
    flight += 1;
    flyingTo = null;
    void map.flyTo({
      cursor: home.cursor,
      distance: home.distance,
      yaw: home.yaw,
      pitch: home.pitch,
    });
  }

  /**
   * The map's own panel about a system opens beside the map only where there
   * is room for both. Under 1400 px of window the map turns its panels into
   * drawers that open over it, and in a narrower map window the panel covers
   * most of what was asked to be shown. There the chosen system is flown to
   * and marked by its range, and its panel opens when the player selects it.
   */
  const PANEL_ROOM_PX = 1240;
  function panelFits(): boolean {
    return (
      (stage?.clientWidth ?? 0) >= PANEL_ROOM_PX &&
      window.matchMedia?.('(max-width: 1399px)').matches !== true
    );
  }

  function panelOf(system: RealSystem): SystemDetails | null {
    const id64 = system.id64;
    if (!id64) return null;
    const details = detailsFor(given.showing, given.journeys, id64);
    if (!details) return null;
    const inspect = {
      label: 'INSPECT',
      onSelect: () => onInspect?.(id64),
    };
    const show = {
      label: 'SHOW ON MAP',
      onSelect: () =>
        onShowOnMap?.({
          id64,
          name: system.name,
          x: system.position[0],
          y: system.position[1],
          z: system.position[2],
          star: system.primaryStar ?? null,
        }),
    };
    return {
      description: details.description,
      values: [...details.values],
      actions: details.kind === 'chosen' ? [inspect] : [show, inspect],
    };
  }

  /**
   * What the map's panel says about a system. The map asks once, when the
   * panel opens on the system. For the chosen system whose stars are still
   * being looked for, the answer is given when they are in, so the panel is
   * right the first time and need not be opened again.
   */
  function panelFor(
    system: RealSystem,
    signal: AbortSignal,
  ): SystemDetails | Promise<SystemDetails | null> | null {
    const looking =
      given.showing?.starsState === 'loading' &&
      given.showing.chosen.id64 === system.id64;
    if (!looking) return panelOf(system);
    return new Promise((settle) => {
      const mine = { system, settle };
      waiting?.settle(null);
      waiting = mine;
      // The panel moved on to another system, or the map was taken down.
      signal.addEventListener('abort', () => {
        if (waiting === mine) waiting = null;
        settle(null);
      });
    });
  }

  /**
   * Make sure what the map is handed gets drawn.
   *
   * The map rests when nothing moves. A change wakes it, but only for about
   * a second: if the page is busy for longer than that before its next
   * frame (as it is just after opening, on a slow device), the map goes back
   * to rest without drawing the change, and it stays undrawn until the
   * camera moves. Seen on a map opened where it was left: no markers.
   *
   * An animation frame only comes once the page is free to draw, so the map
   * is woken once more from the next one. Setting a switch to the value it
   * already has is the map's wake with nothing else attached: it ends no
   * flight and no zoom, as moving the camera would.
   */
  let wakeAsked = 0;
  function wakeWhenFree(): void {
    if (wakeAsked !== 0 || typeof requestAnimationFrame !== 'function') return;
    wakeAsked = requestAnimationFrame(() => {
      wakeAsked = 0;
      const held = map;
      held?.setSystemNamesVisible(held.areSystemNamesVisible());
    });
  }

  /**
   * The groups the player has switched off in the map's own Filters panel:
   * their markers, and their lines.
   */
  function groupsSwitchedOff(held: GalaxyMap): {
    markers: string[];
    shapes: string[];
  } {
    const off = { markers: [] as string[], shapes: [] as string[] };
    for (let index = 0; index < held.categoryCount(); index += 1) {
      const name = held.getCategory(index)?.name;
      if (!name) continue;
      if (!held.isCategoryVisible(name)) off.markers.push(name);
      if (!held.isShapeCategoryVisible(name)) off.shapes.push(name);
    }
    return off;
  }

  function warnOfRejected(
    report: { rejected: readonly unknown[] },
    asked: number,
  ): void {
    if (report.rejected.length > 0)
      console.warn(
        `Galaxy map: ${report.rejected.length} of ${asked} systems were not drawn`,
        report.rejected.slice(0, 5),
      );
  }

  /** The shaded balls and the lines. A line names its group, so groups go first. */
  function addShapes(held: GalaxyMap, content: MapContent): void {
    held.addSpheres(content.spheres.map((sphere) => ({ ...sphere })));
    if (content.lines.length === 0) return;
    const report = held.addLines(
      content.lines.map((line) => ({
        ...line,
        points: line.points.map((point) => [...point] as const),
      })),
    );
    if (report.rejected.length > 0)
      console.warn(
        `Galaxy map: ${report.rejected.length} of ${content.lines.length} lines were not drawn`,
        report.rejected.slice(0, 5),
      );
  }

  const panelText = (from: typeof given, id64: string) =>
    JSON.stringify(detailsFor(from.showing, from.journeys, id64));

  /**
   * Hand the map what it should show.
   *
   * Where there is only more to show (a system's stars arriving), it is
   * added to what is drawn, and the selection, the camera and any flight
   * are left exactly as they are. Where something has to come off (another
   * system chosen, My journeys switched off) the map can only be emptied
   * and filled again, which drops its selection: what the player had
   * selected and switched off is then put back.
   */
  function give(next: typeof given, content: MapContent): void {
    const held = map;
    if (!held) return;
    wakeWhenFree();
    const before = given;
    const chosen = next.showing?.chosen ?? null;
    const chosenChanged = (chosen?.id64 ?? null) !== shownId;
    const selectedBefore = held.getSelection()?.id64 ?? null;
    given = next;
    shownId = chosen?.id64 ?? null;
    /** The system whose panel is told, in this call, what it now says. */
    let told: string | null = null;

    const more = drawn && whatToAdd(drawn, content);
    if (more) {
      if (more.groups.length > 0)
        held.addCategories(more.groups.map((group) => ({ ...group })));
      if (more.records.length > 0)
        warnOfRejected(
          held.addSystems(more.records.map((record) => ({ ...record }))),
          more.records.length,
        );
      if (more.shapes) {
        held.clearShapes();
        addShapes(held, content);
      }
    } else {
      const off = groupsSwitchedOff(held);
      const viewBefore = held.getView();
      held.clearSystemsAndCategories();
      held.clearShapes();
      held.addCategories(content.groups.map((group) => ({ ...group })));
      warnOfRejected(
        held.addSystems(content.records.map((record) => ({ ...record }))),
        content.records.length,
      );
      addShapes(held, content);
      for (const name of off.markers) held.setCategoryVisible(name, false);
      for (const name of off.shapes) held.setShapeCategoryVisible(name, false);
      const keep =
        !chosenChanged &&
        selectedBefore !== null &&
        content.records.some((record) => record.id64 === selectedBefore);
      if (keep) {
        // Selecting moves the camera: a flight of ours to the chosen system
        // carries on, otherwise the camera goes back where the player had it.
        held.setSelection(selectedBefore);
        told = selectedBefore;
        if (chosen && flyingTo === chosen.id64) fly(chosen);
        else held.setView(viewBefore);
      }
    }
    drawn = content;

    // The stars a panel was waiting for are in (or will not come).
    if (
      waiting &&
      (next.showing?.starsState !== 'loading' ||
        next.showing.chosen.id64 !== waiting.system.id64)
    ) {
      const { system, settle } = waiting;
      waiting = null;
      told = system.id64 ?? null;
      settle(panelOf(system));
    }
    // A panel told in this call says what is known now.
    if (told !== null && told === wordsOwed) wordsOwed = null;

    if (chosen && chosenChanged) {
      // A new system was chosen in Find: fly to it, and open the map's
      // panel on it where the panel has room beside the map.
      wordsOwed = null;
      held.setSelection(panelFits() ? chosen.id64 : null);
      fly(chosen);
      return;
    }
    // The same showing, and the panel is open on a system it would now say
    // something else about. The map reads a panel once, so it has to be
    // opened again: at once where the camera is still or the player's, and
    // on landing where a flight of ours to the chosen system is under way,
    // which opening a panel would otherwise stop and start again.
    const selected = held.getSelection()?.id64 ?? null;
    if (
      selected &&
      selected !== told &&
      panelText(before, selected) !== panelText(next, selected)
    ) {
      if (chosen && flyingTo === chosen.id64) wordsOwed = selected;
      else readAgain(held, selected);
    }
  }

  $effect(() => {
    const next = { showing, journeys };
    const content = mapContent(showing, journeys);
    if (phase !== 'ready' || !onScreen) return;
    untrack(() => give(next, content));
  });

  function allowKeys(on: boolean): void {
    map?.setInteraction({ keys: on });
  }

  /**
   * Hand a finished map's graphics memory back at once. A browser keeps the
   * memory of a canvas that has left the page until it next tidies up, and
   * allows only so many at a time: going to Inspect and back a couple of
   * dozen times would otherwise pile them up.
   */
  function releaseGraphics(used: HTMLCanvasElement): void {
    // Only where there are real graphics (not in the tests' browser).
    if (typeof WebGL2RenderingContext === 'undefined') return;
    try {
      used
        .getContext('webgl2')
        ?.getExtension('WEBGL_lose_context')
        ?.loseContext();
    } catch {
      // Nothing to hand back.
    }
  }

  onMount(() => {
    let gone = false;
    let made: GalaxyMap | null = null;
    void (async () => {
      try {
        const [{ createGalaxyMap }, { nebulae }] = await Promise.all([
          import('@elite-dangerous-almanac/galaxy-map'),
          import('@elite-dangerous-almanac/galaxy-map/nebulae'),
        ]);
        if (gone || !canvas) return;
        made = createGalaxyMap(canvas, {
          systemNames: true,
          nebulae,
          // The map's movement keys (W A S D R F Q E) are heard anywhere on
          // the page, so they are on only while the map is being used.
          interaction: { keys: false },
          ...(resume ? { startView: { ...resume.view } } : {}),
          hud: {
            title: 'GALAXY MAP',
            details: panelFor,
            // The map's own RANGE tile measures from the camera's centre,
            // which is on a selected system once the camera has landed: it
            // would read "0 LY" on every panel. A star's panel gives its
            // distance from the chosen system instead (`map-content`).
            infoFields: { range: false },
          },
        });
        home = resume?.home ?? made.getView();
        await made.ready;
        if (gone) return;
        map = made;
        // Opening where it was left: the same system is not flown to again.
        shownId = resume?.shownId ?? null;
        // The effect above hands the map its systems once it is ready.
        phase = 'ready';
      } catch (error) {
        if (gone) return;
        made?.dispose();
        made = null;
        // The map's own reason, without its pointer to its developers' notes.
        problem = (error instanceof Error ? error.message : String(error))
          .replace(/\s*See \.devcontainer\/README\.md\.?\s*$/u, '')
          .trim();
        phase = 'failed';
      }
    })();
    const used = canvas;
    return () => {
      gone = true;
      if (wakeAsked !== 0) cancelAnimationFrame(wakeAsked);
      // A flight of ours that was still on its way has not shown its system
      // yet: opening there again must fly the rest of the way.
      if (map && home)
        onLeave?.({
          view: map.getView(),
          home,
          shownId: flyingTo === null ? shownId : null,
        });
      map = null;
      if (!made) return;
      made.dispose();
      if (used) releaseGraphics(used);
    };
  });
</script>

<div class="galaxy-map" data-galaxy-map={phase}>
  <!-- The map's own movement keys work while this has the focus or the
       pointer. Its panels are built beside the canvas by the map itself: they
       are ordinary buttons and text, so this is a group, not an application,
       and a screen reader can read them. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div
    class="galaxy-map-stage"
    bind:this={stage}
    role="group"
    aria-label="Galaxy map"
    aria-describedby={`${uid}-keys`}
    tabindex="0"
    onpointerenter={() => allowKeys(true)}
    onpointerleave={() => allowKeys(stage?.matches(':focus-within') ?? false)}
    onfocusin={() => allowKeys(true)}
    onfocusout={() => allowKeys(stage?.matches(':hover') ?? false)}
  >
    <canvas bind:this={canvas}></canvas>
    <p id={`${uid}-keys`} class="galaxy-map-keys">
      Drag to turn the map, right-drag to move it, scroll to zoom. With the map
      in focus, W A S D R and F move it and Q and E turn it.
    </p>
    {#if phase === 'starting'}
      <p class="galaxy-map-note" role="status">Starting the galaxy map…</p>
    {:else if phase === 'failed'}
      <div class="galaxy-map-note" role="alert">
        <p>
          <strong>The galaxy map could not start on this device.</strong>
        </p>
        <p>
          It needs a browser with WebGL2 and a graphics card it can use. Find,
          Inspect and Plan work without it.
        </p>
        {#if problem}<p class="galaxy-map-problem">{problem}</p>{/if}
      </div>
    {/if}
  </div>
  <p class="galaxy-map-notice">
    Map:
    <a href="https://github.com/Elite-Dangerous-Almanac/Galaxy-Map"
      >Galaxy-Map</a
    >. Required Notice: Copyright 2026 Dark Session
    (https://github.com/Elite-Dangerous-Almanac/Galaxy-Map) ·
    <a href="https://polyformproject.org/licenses/noncommercial/1.0.0"
      >PolyForm Noncommercial License 1.0.0</a
    >
    · <a href={asset('/licences/galaxy-map-LICENSE.txt')}>terms</a> ·
    <a href={asset('/licences/galaxy-map-THIRD_PARTY_NOTICES.txt')}>notices</a>
    · Elite Dangerous game data and visuals are the property of Frontier Developments
    plc.
  </p>
</div>

<style>
  .galaxy-map {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
  }
  .galaxy-map-stage {
    position: relative;
    flex: 1;
    min-height: 16rem;
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: var(--radius-md, 0.5rem);
    background: var(--bg-void);
  }
  .galaxy-map-keys {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
  .galaxy-map-stage:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: -2px;
  }
  .galaxy-map-note {
    position: absolute;
    inset: 50% auto auto 50%;
    z-index: 2;
    width: min(30rem, calc(100% - 2rem));
    margin: 0;
    padding: 0.75rem 1rem;
    transform: translate(-50%, -50%);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-md, 0.5rem);
    background: var(--panel);
    color: var(--text);
    text-align: center;
  }
  .galaxy-map-note p {
    margin: 0.25rem 0;
  }
  .galaxy-map-problem {
    color: var(--text-muted, inherit);
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }
  .galaxy-map-notice {
    flex-shrink: 0;
    margin: 0;
    padding: 0.25rem 0.5rem 0;
    color: var(--text-muted, inherit);
    font-size: 0.7rem;
    line-height: 1.35;
  }
  .galaxy-map-notice a {
    color: inherit;
  }
</style>
