import { cleanup, render, screen } from '@testing-library/svelte';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import solBox from './fixtures/map-systems-sol-box.json';
import GalaxyMap, { type MapResume } from './GalaxyMap.svelte';
import {
  failNextMap,
  FAKE_HOME,
  fakeMaps,
  resetFakeMaps,
  type FakeMap,
} from './fake-almanac';
import {
  JOURNEYS_GROUP,
  starsInRange,
  type BoxSystem,
  type Journey,
  type MapSystem,
  type Showing,
} from './map-content';

// The map draws with WebGL2, which the test browser does not have. A
// stand-in keeps what the page hands the map (see fake-almanac.ts).
vi.mock('@elite-dangerous-almanac/galaxy-map', () => import('./fake-almanac'));
vi.mock('@elite-dangerous-almanac/galaxy-map/nebulae', () => ({
  nebulae: { the: 'library’s own nebulae' },
}));

// The live site's real answer for the 30 ly box around Sol on 13 Nov 2026.
const SOL_BOX: BoxSystem[] = solBox.response.systems.map((system) => ({
  id64: String(system.id64),
  name: system.name,
  x: system.x,
  y: system.y,
  z: system.z,
  mainStarClass: system.main_star_class,
}));
const SOL: MapSystem = {
  id64: '10477373803',
  name: 'Sol',
  x: 0,
  y: 0,
  z: 0,
  star: 'G2',
};
const ALPHA = SOL_BOX.find((system) => system.name === 'Alpha Centauri')!;
const BARNARD = SOL_BOX.find((system) => system.name === "Barnard's Star")!;
const asChosen = (system: BoxSystem): MapSystem => ({
  id64: system.id64,
  name: system.name,
  x: system.x,
  y: system.y,
  z: system.z,
  star: system.mainStarClass ?? null,
});

const looking = (chosen: MapSystem = SOL): Showing => ({
  chosen,
  facts: null,
  stars: [],
  starsState: 'loading',
  starsCutShort: false,
});
const found = (chosen: MapSystem = SOL): Showing => ({
  ...looking(chosen),
  stars: starsInRange(SOL_BOX, chosen),
  starsState: 'ready',
});
// A real system far from Sol, with a visit that is this test's own.
const VISITED: Journey = {
  id64: '3068561197395',
  name: 'Wregoe SJ-I d9-89',
  x: 308.4375,
  y: -23.25,
  z: -253.0625,
  visits: 2,
  firstVisit: '2026-02-01T00:00:00Z',
  lastVisit: '2026-02-03T00:00:00Z',
};

async function started(
  props: Partial<{
    showing: Showing | null;
    journeys: readonly Journey[] | null;
    resume: MapResume | null;
    onLeave: (resume: MapResume) => void;
    onScreen: boolean;
    onShowOnMap: (system: MapSystem) => void;
    onInspect: (id64: string) => void;
  }> = {},
) {
  const view = render(GalaxyMap, { props: { showing: null, ...props } });
  await vi.waitFor(() =>
    expect(
      view.container.querySelector('[data-galaxy-map="ready"]'),
    ).not.toBeNull(),
  );
  return { ...view, map: fakeMaps.at(-1) as FakeMap };
}

/** Let the promises of ended flights run. */
const settled = () => new Promise((done) => setTimeout(done, 0));

// The width of the map beside the Finder on a 1920 px screen, as measured on
// the built page: wide enough for the map's own panel to open beside it.
const WIDE_PX = 1446;
let mapWidth = WIDE_PX;

beforeEach(() => {
  resetFakeMaps();
  mapWidth = WIDE_PX;
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(
    () => mapWidth,
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('starting the map', () => {
  it('opens on the whole galaxy with nothing on it, nebulae on and our own panel', async () => {
    const { map, container } = await started();
    expect(fakeMaps).toHaveLength(1);
    expect(map.canvas).toBe(container.querySelector('canvas'));
    expect(map.options.nebulae).toEqual({ the: 'library’s own nebulae' });
    expect(map.options.systemNames).toBe(true);
    expect(map.options.startView).toBeUndefined();
    expect(map.options.hud).toMatchObject({ title: 'GALAXY MAP' });
    // The map's own RANGE tile would read "0 LY" on every landed selection.
    expect(map.options.hud).toMatchObject({ infoFields: { range: false } });
    expect(map.systemCount()).toBe(0);
    expect(map.flights).toHaveLength(0);
    expect(map.getView()).toEqual(FAKE_HOME);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says so in plain words when the map cannot start, and stops', async () => {
    // The map's own words (0.7.1) for a browser that draws on the processor.
    failNextMap(
      'This map needs hardware rendering. The browser reports "SwiftShader", which draws on the processor. See .devcontainer/README.md.',
    );
    const { container } = render(GalaxyMap, { props: { showing: found() } });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'The galaxy map could not start on this device.',
    );
    expect(alert).toHaveTextContent('Find, Inspect and Plan work without it.');
    // Its reason is passed on, without its pointer to its developers' notes.
    expect(alert).toHaveTextContent(
      'The browser reports "SwiftShader", which draws on the processor.',
    );
    expect(alert).not.toHaveTextContent('devcontainer');
    expect(
      container.querySelector('[data-galaxy-map="failed"]'),
    ).not.toBeNull();
    expect(fakeMaps[0].disposed).toBe(true);
    expect(fakeMaps[0].systemCount()).toBe(0);
  });

  it('hears its movement keys only while the pointer or the focus is on it', async () => {
    const { map, container } = await started();
    const stage = container.querySelector<HTMLElement>('.galaxy-map-stage')!;
    // The keys are heard anywhere on the page, so they start switched off.
    expect(map.interaction.keys).toBe(false);
    expect(map.interaction.zoom).toBe(true);
    stage.dispatchEvent(new PointerEvent('pointerenter'));
    expect(map.interaction.keys).toBe(true);
    stage.dispatchEvent(new PointerEvent('pointerleave'));
    expect(map.interaction.keys).toBe(false);
    // The map can be reached with the Tab key, and says what the keys do.
    expect(stage.tabIndex).toBe(0);
    expect(stage).toHaveAccessibleName('Galaxy map');
    expect(stage).toHaveAccessibleDescription(/W A S D R and F move it/);
    // A group, so a screen reader can read the map's own panels inside it.
    expect(stage.getAttribute('role')).toBe('group');
    stage.focus();
    expect(map.interaction.keys).toBe(true);
    stage.blur();
    expect(map.interaction.keys).toBe(false);
  });

  it('is taken down when the page leaves', async () => {
    const { map, unmount } = await started();
    unmount();
    expect(map.disposed).toBe(true);
  });

  it('hands its graphics memory back when the page leaves, after the map is taken down', async () => {
    // A browser with real graphics: the tests' own has none.
    const order: string[] = [];
    const loseContext = vi.fn(() => order.push('memory handed back'));
    vi.stubGlobal('WebGL2RenderingContext', class {});
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation(
        () =>
          ({
            getExtension: (name: string) =>
              name === 'WEBGL_lose_context' ? { loseContext } : null,
          }) as unknown as RenderingContext,
      );
    try {
      const { map, unmount } = await started();
      const dispose = map.dispose.bind(map);
      map.dispose = () => {
        order.push('map taken down');
        dispose();
      };
      expect(loseContext).not.toHaveBeenCalled();
      unmount();
      expect(order).toEqual(['map taken down', 'memory handed back']);
      expect(getContext).toHaveBeenCalledWith('webgl2');
    } finally {
      getContext.mockRestore();
      vi.unstubAllGlobals();
    }
  });
});

describe('a system chosen in Find', () => {
  it('is put on the map at once, selected, with its 15 ly range, and flown to', async () => {
    const { map } = await started({ showing: looking() });
    expect([...map.systems.keys()]).toEqual([SOL.id64]);
    expect(map.getSelection()?.name).toBe('Sol');
    expect(map.spheres).toEqual([
      expect.objectContaining({ position: [0, 0, 0], radius: 15 }),
    ]);
    expect(map.flights).toHaveLength(1);
    expect(map.flights[0].target).toEqual({
      cursor: [0, 0, 0],
      distance: 70,
      pitch: 35,
    });
    expect(map.isFlying()).toBe(true);
  });

  it('gets its real stars when they arrive, each star class its own group, with nothing taken off the map first', async () => {
    const { map, rerender } = await started({ showing: looking() });
    // The panel is open on Sol and waits to be told how many stars it has.
    expect(map.opened).toEqual([SOL.id64]);
    expect(await map.panelIsWaiting()).toBe(true);
    const fills = map.fills;
    await rerender({ showing: found() });
    expect(map.systemCount()).toBe(51);
    // Star classes differ: the M dwarfs and the G stars are separate groups
    // in separate colours, and Sol is among the G stars.
    expect(map.namesIn('G class star')).toContain('Sol');
    expect(map.namesIn('G class star')).toContain('Alpha Centauri');
    expect(map.namesIn('M class star')).toContain("Barnard's Star");
    expect(map.groups.get('G class star')!.color).not.toEqual(
      map.groups.get('M class star')!.color,
    );
    expect(map.groups.size).toBe(9);
    // Sol's own class leads the map's list, as it did before the stars came.
    expect([...map.groups.keys()][0]).toBe('G class star');
    // The stars were added to what was there: the map was not emptied, the
    // selection was not touched, and the one flight is still on its way.
    expect(map.fills).toBe(fills);
    expect(map.opened).toEqual([SOL.id64]);
    expect(map.getSelection()?.name).toBe('Sol');
    expect(map.flights).toHaveLength(1);
    expect(map.isFlying()).toBe(true);
    // The waiting panel is told, once, with the count.
    expect(await map.panelIsWaiting()).toBe(false);
    expect(
      (await map.panel(SOL.id64))!.values!.find(
        (value) => value.label === 'STARS IN RANGE',
      )!.value,
    ).toBe('50');
    map.land();
    await settled();
    expect(map.getView()).toMatchObject({ cursor: [0, 0, 0], distance: 70 });
  });

  it('leaves the camera alone if the player took it before the stars arrived', async () => {
    const { map, rerender } = await started({ showing: looking() });
    map.playerMovesTo({ cursor: [500, 0, 500], distance: 2_000 });
    await settled();
    await rerender({ showing: found() });
    expect(map.systemCount()).toBe(51);
    expect(map.flights).toHaveLength(1);
    expect(map.getView()).toMatchObject({
      cursor: [500, 0, 500],
      distance: 2_000,
    });
    expect(map.getSelection()?.name).toBe('Sol');
  });

  it('flies to the next system chosen, and shows only that system’s stars', async () => {
    const { map, rerender } = await started({ showing: found() });
    map.land();
    await settled();
    const next = asChosen(ALPHA);
    await rerender({ showing: found(next) });
    expect(map.getSelection()?.name).toBe('Alpha Centauri');
    expect(map.flights.at(-1)!.target).toMatchObject({
      cursor: [ALPHA.x, ALPHA.y, ALPHA.z],
      distance: 70,
    });
    expect(map.spheres).toHaveLength(1);
    expect(map.spheres[0]).toMatchObject({
      position: [ALPHA.x, ALPHA.y, ALPHA.z],
    });
    // Sol is 4.4 ly from Alpha Centauri, so it is now one of the stars.
    expect((await map.panel(SOL.id64))!.description).toBe(
      'A real star within 15 ly of Alpha Centauri.',
    );
    expect(map.systemCount()).toBe(starsInRange(SOL_BOX, next).length + 1);
  });

  it('leaves the map exactly as the player has it when the same answer comes again', async () => {
    const { map, rerender } = await started({
      showing: found(),
      journeys: [VISITED],
    });
    map.land();
    await settled();
    map.playerSelects(BARNARD.id64);
    map.playerMovesTo({ cursor: [1, 2, 3], distance: 40 });
    const before = {
      fills: map.fills,
      opened: map.opened.length,
      view: map.getView(),
    };
    // A refreshed search hands the page the same systems as new objects.
    await rerender({ showing: found(), journeys: [{ ...VISITED }] });
    expect(map.fills).toBe(before.fills);
    expect(map.opened).toHaveLength(before.opened);
    expect(map.getView()).toEqual(before.view);
    expect(map.getSelection()?.name).toBe("Barnard's Star");
    expect(map.flights).toHaveLength(1);
    expect(map.systemCount()).toBe(52);
  });

  it('tells a waiting panel that the stars did not load', async () => {
    const { map, rerender } = await started({ showing: looking() });
    await rerender({ showing: { ...looking(), starsState: 'failed' } });
    expect(await map.panelIsWaiting()).toBe(false);
    expect(
      (await map.panel(SOL.id64))!.values!.find(
        (value) => value.label === 'STARS IN RANGE',
      )!.value,
    ).not.toMatch(/\d/);
    expect(map.opened).toEqual([SOL.id64]);
    expect(map.flights).toHaveLength(1);
  });

  const withFacts = (): Showing => ({
    ...found(),
    facts: { potential: 53, primary: null, secondary: null, claim: 'free' },
  });
  const saidBy =
    (map: FakeMap) =>
    async (label: string): Promise<string | undefined> =>
      (await map.panel(SOL.id64))!.values!.find(
        (value) => value.label === label,
      )?.value;

  it('opens the panel again, and draws nothing again, when what it says changes after it was told', async () => {
    const { map, rerender } = await started({ showing: found() });
    const said = saidBy(map);
    map.land();
    await settled();
    expect(await said('CLAIM')).toBeUndefined();
    const before = { fills: map.fills, adds: map.adds };
    const landedAt = map.getView();
    // What Find says of Sol arrives after the panel opened.
    await rerender({ showing: withFacts() });
    expect(map.fills).toBe(before.fills);
    expect(map.adds).toBe(before.adds);
    // The map reads a panel once, so it was opened a second time.
    expect(map.opened).toEqual([SOL.id64, SOL.id64]);
    expect(await said('CLAIM')).toBe('free');
    expect(await said('STARS IN RANGE')).toBe('50');
    // Opening it moves the camera: it was put back, with no flight.
    expect(map.flights).toHaveLength(1);
    expect(map.getView()).toEqual(landedAt);
    expect(map.getSelection()?.name).toBe('Sol');
  });

  it('leaves the flight alone when what the panel says changes on the way, and opens the panel again on landing', async () => {
    const { map, rerender } = await started({ showing: found() });
    const said = saidBy(map);
    // What Find says of Sol arrives while the map is flying there. Opening
    // the panel again now would stop the flight and start another.
    await rerender({ showing: withFacts() });
    expect(map.opened).toEqual([SOL.id64]);
    expect(map.flights).toHaveLength(1);
    expect(map.isFlying()).toBe(true);
    expect(await said('CLAIM')).toBeUndefined();

    map.land();
    await settled();
    expect(map.opened).toEqual([SOL.id64, SOL.id64]);
    expect(await said('CLAIM')).toBe('free');
    // Still one flight, and the camera is where it landed.
    expect(map.flights).toHaveLength(1);
    expect(map.getView()).toMatchObject({ cursor: [0, 0, 0], distance: 70 });
    expect(map.getSelection()?.name).toBe('Sol');
  });

  it('opens the panel again where the player took the camera on the way, and leaves the camera with the player', async () => {
    const { map, rerender } = await started({ showing: found() });
    const said = saidBy(map);
    await rerender({ showing: withFacts() });
    map.playerMovesTo({ cursor: [40, 0, -30], distance: 900 });
    await settled();
    expect(map.opened).toEqual([SOL.id64, SOL.id64]);
    expect(await said('CLAIM')).toBe('free');
    expect(map.flights).toHaveLength(1);
    expect(map.getView()).toMatchObject({
      cursor: [40, 0, -30],
      distance: 900,
    });
  });

  it('opens the panel once where the stars come in after what Find says, both on the way', async () => {
    const { map, rerender } = await started({ showing: looking() });
    const said = saidBy(map);
    // What Find says arrives first: the panel has not been told yet, as it
    // waits for the stars, so nothing is owed for it.
    await rerender({ showing: { ...looking(), facts: withFacts().facts } });
    await rerender({ showing: withFacts() });
    expect(await said('CLAIM')).toBe('free');
    expect(await said('STARS IN RANGE')).toBe('50');
    map.land();
    await settled();
    expect(map.opened).toEqual([SOL.id64]);
    expect(map.flights).toHaveLength(1);
  });

  it('does not open the panel again on landing for a system the player has since closed it on', async () => {
    const { map, rerender } = await started({ showing: found() });
    await rerender({ showing: withFacts() });
    map.setSelection(null);
    map.land();
    await settled();
    expect(map.opened).toEqual([SOL.id64]);
    expect(map.getSelection()).toBeNull();
  });

  it('flies to the chosen system without opening the map’s panel where the panel would cover the map', async () => {
    // The map beside the Finder on a 1600 px screen.
    mapWidth = 1150;
    const { map, rerender } = await started({ showing: looking() });
    expect([...map.systems.keys()]).toEqual([SOL.id64]);
    expect(map.getSelection()).toBeNull();
    expect(map.opened).toEqual([]);
    expect(map.flights).toHaveLength(1);
    expect(map.spheres).toHaveLength(1);
    await rerender({ showing: found() });
    expect(map.systemCount()).toBe(51);
    expect(map.getSelection()).toBeNull();
    expect(map.flights).toHaveLength(1);
    // The player selects it on the map: the panel opens, with its button.
    map.playerSelects(SOL.id64);
    expect((await map.panel(SOL.id64))!.actions!.map((a) => a.label)).toEqual([
      'INSPECT',
    ]);
  });

  it('does not open the panel either where the window is narrow enough for the map to use drawers', async () => {
    mapWidth = 1380;
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 1399px)',
    }));
    const { map } = await started({ showing: found() });
    expect(map.getSelection()).toBeNull();
    expect(map.flights).toHaveLength(1);
    vi.unstubAllGlobals();
  });

  it('takes everything off the map when nothing is chosen any more', async () => {
    const { map, rerender } = await started({ showing: found() });
    await rerender({ showing: null });
    expect(map.systemCount()).toBe(0);
    expect(map.spheres).toEqual([]);
    expect(map.getSelection()).toBeNull();
  });
});

describe('the map’s own panel', () => {
  it('gives the chosen system a button to Inspect, and a star a button to be shown in its own right', async () => {
    const onInspect = vi.fn();
    const onShowOnMap = vi.fn();
    const { map } = await started({
      showing: found(),
      onInspect,
      onShowOnMap,
    });
    const chosen = (await map.panel(SOL.id64))!;
    expect(chosen.actions!.map((action) => action.label)).toEqual(['INSPECT']);
    chosen.actions![0].onSelect(map.systems.get(SOL.id64)!);
    expect(onInspect).toHaveBeenCalledWith(SOL.id64);

    const star = (await map.panel(BARNARD.id64))!;
    expect(star.description).toBe('A real star within 15 ly of Sol.');
    expect(star.actions!.map((action) => action.label)).toEqual([
      'SHOW ON MAP',
      'INSPECT',
    ]);
    star.actions![0].onSelect(map.systems.get(BARNARD.id64)!);
    expect(onShowOnMap).toHaveBeenCalledWith(asChosen(BARNARD));
    star.actions![1].onSelect(map.systems.get(BARNARD.id64)!);
    expect(onInspect).toHaveBeenLastCalledWith(BARNARD.id64);
  });
});

describe('My journeys', () => {
  it('adds the visited systems as their own group without moving the camera or the selection', async () => {
    const { map, rerender } = await started({ showing: found() });
    map.land();
    await settled();
    map.playerSelects(BARNARD.id64);
    map.playerMovesTo({ cursor: [1, 2, 3], distance: 40 });
    const before = map.getView();

    await rerender({ showing: found(), journeys: [VISITED] });
    expect(map.namesIn(JOURNEYS_GROUP)).toEqual(['Wregoe SJ-I d9-89']);
    expect(map.groups.get(JOURNEYS_GROUP)).toMatchObject({
      markerStyle: 'disc',
      visible: true,
    });
    expect(map.systemCount()).toBe(52);
    expect(map.getSelection()?.name).toBe("Barnard's Star");
    expect(map.getView()).toEqual(before);
    expect(map.flights).toHaveLength(1);
    expect((await map.panel(VISITED.id64))!.values![0]).toEqual({
      label: 'YOUR VISITS',
      value: '2',
    });

    // Switched off again: the visited systems go, the showing stays.
    await rerender({ showing: found(), journeys: null });
    expect(map.groups.has(JOURNEYS_GROUP)).toBe(false);
    expect(map.systemCount()).toBe(51);
    expect(map.getView()).toEqual(before);
  });

  it('shows with nothing chosen, and keeps a group the player switched off switched off', async () => {
    const { map, rerender } = await started({ journeys: [VISITED] });
    expect(map.namesIn(JOURNEYS_GROUP)).toEqual(['Wregoe SJ-I d9-89']);
    expect(map.flights).toHaveLength(0);
    // The player switches the group off in the map's own Filters panel…
    map.setCategoryVisible(JOURNEYS_GROUP, false);
    // …and then chooses a system in Find, which fills the map again.
    await rerender({ showing: found(), journeys: [VISITED] });
    expect(map.isCategoryVisible(JOURNEYS_GROUP)).toBe(false);
    expect(map.isCategoryVisible('G class star')).toBe(true);
  });
});

describe('a map that cannot be seen', () => {
  it('is given nothing and flies nowhere until it is seen, then catches up', async () => {
    const { map, rerender } = await started({
      showing: looking(),
      onScreen: false,
    });
    await rerender({ showing: found(), onScreen: false });
    await rerender({ showing: found(), journeys: [VISITED], onScreen: false });
    expect(map.fills).toBe(0);
    expect(map.systemCount()).toBe(0);
    expect(map.flights).toHaveLength(0);
    expect(map.getSelection()).toBeNull();

    await rerender({ showing: found(), journeys: [VISITED], onScreen: true });
    // Everything at once: one fill, one flight, the system selected.
    expect(map.fills).toBe(1);
    expect(map.systemCount()).toBe(52);
    expect(map.getSelection()?.name).toBe('Sol');
    expect(map.flights).toHaveLength(1);
    expect(map.flights[0].target).toMatchObject({
      cursor: [0, 0, 0],
      distance: 70,
    });
  });

  it('keeps what it shows while it is away, and moves on when it is seen again', async () => {
    const { map, rerender } = await started({ showing: found() });
    map.land();
    await settled();
    const fills = map.fills;
    await rerender({ showing: found(asChosen(ALPHA)), onScreen: false });
    expect(map.fills).toBe(fills);
    expect(map.getSelection()?.name).toBe('Sol');
    expect(map.flights).toHaveLength(1);
    await rerender({ showing: found(asChosen(ALPHA)), onScreen: true });
    expect(map.getSelection()?.name).toBe('Alpha Centauri');
    expect(map.flights).toHaveLength(2);
  });
});

describe('the camera buttons of the page', () => {
  it('does not start a flight again that is already on its way to the chosen system', async () => {
    const { map, component } = await started({ showing: found() });
    expect(map.flights).toHaveLength(1);
    component.centre();
    expect(map.flights).toHaveLength(1);
    map.land();
    await settled();
    // Landed: now there is somewhere to fly back from.
    component.centre();
    expect(map.flights).toHaveLength(2);
  });

  it('fly back to the chosen system, and out to the whole galaxy', async () => {
    const { map, component } = await started({ showing: found() });
    map.land();
    await settled();
    map.playerMovesTo({ cursor: [900, 0, 0], distance: 5_000 });
    component.centre();
    expect(map.flights.at(-1)!.target).toEqual({
      cursor: [0, 0, 0],
      distance: 70,
      pitch: 35,
    });
    component.wholeGalaxy();
    expect(map.flights.at(-1)!.target).toEqual(FAKE_HOME);
    map.land();
    await settled();
    expect(map.getView()).toEqual(FAKE_HOME);
  });
});

describe('making sure what the map is handed gets drawn', () => {
  // The real map rests when nothing moves, and a change wakes it for about a
  // second only. On a page too busy to draw within that, the change was never
  // drawn (seen in a browser: a map opened where it was left had no markers
  // until the camera moved).
  let frames: (() => void)[];
  let cancelled: number[];
  beforeEach(() => {
    frames = [];
    cancelled = [];
    vi.stubGlobal('requestAnimationFrame', (run: () => void) => {
      frames.push(run);
      return frames.length;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => cancelled.push(id));
  });
  afterEach(() => vi.unstubAllGlobals());
  const nextFrame = () => {
    const due = frames;
    frames = [];
    for (const run of due) run();
  };

  it('wakes the map from the next animation frame, without moving it or touching what is selected', async () => {
    const { map } = await started({ showing: found() });
    // Handed its systems, and not yet woken a second time.
    expect(map.systemCount()).toBe(51);
    expect(map.wakes).toBe(0);
    const before = {
      view: map.getView(),
      flights: map.flights.length,
      selected: map.getSelection()?.name,
      names: map.areSystemNamesVisible(),
    };
    nextFrame();
    expect(map.wakes).toBe(1);
    // The wake is the map's names switch set to the value it already had.
    expect(map.areSystemNamesVisible()).toBe(before.names);
    expect(map.getView()).toEqual(before.view);
    expect(map.flights).toHaveLength(before.flights);
    expect(map.getSelection()?.name).toBe(before.selected);
    // Nothing more is asked until the map is handed something again.
    nextFrame();
    expect(map.wakes).toBe(1);
  });

  it('asks for one wake however much is handed before the frame comes, and again after the next change', async () => {
    const { map, rerender } = await started({ showing: looking() });
    await rerender({ showing: found() });
    await rerender({ showing: found(), journeys: [VISITED] });
    expect(frames).toHaveLength(1);
    nextFrame();
    expect(map.wakes).toBe(1);
    await rerender({ showing: found(), journeys: null });
    expect(frames).toHaveLength(1);
    nextFrame();
    expect(map.wakes).toBe(2);
  });

  it('keeps a names switch the player turned off as it is', async () => {
    const { map } = await started({ showing: found() });
    map.namesVisible = false;
    nextFrame();
    expect(map.wakes).toBe(1);
    expect(map.areSystemNamesVisible()).toBe(false);
  });

  it('drops a wake that is still waiting when the page leaves', async () => {
    const { map, unmount } = await started({ showing: found() });
    expect(frames).toHaveLength(1);
    unmount();
    expect(cancelled).toEqual([1]);
    // Were the frame to come anyway, the map that was taken down is left alone.
    nextFrame();
    expect(map.wakes).toBe(0);
  });
});

describe('leaving and coming back', () => {
  it('hands back where the camera was, and opens there again without a flight', async () => {
    const onLeave = vi.fn<(resume: MapResume) => void>();
    const first = await started({ showing: found(), onLeave });
    first.map.land();
    await settled();
    first.map.playerMovesTo({ cursor: [10, 0, 10], distance: 300, yaw: 40 });
    first.unmount();
    expect(onLeave).toHaveBeenCalledTimes(1);
    const resume = onLeave.mock.calls[0][0];
    expect(resume).toEqual({
      view: { cursor: [10, 0, 10], distance: 300, yaw: 40, pitch: 35 },
      home: FAKE_HOME,
      shownId: SOL.id64,
    });

    const second = await started({ showing: found(), resume });
    expect(second.map.options.startView).toEqual(resume.view);
    expect(second.map.systemCount()).toBe(51);
    expect(second.map.flights).toHaveLength(0);
    expect(second.map.getView()).toEqual(resume.view);
    // The whole-galaxy button still knows the view the map first opened on.
    second.component.wholeGalaxy();
    expect(second.map.flights[0].target).toEqual(FAKE_HOME);
  });

  it('left while still flying to a system, it opens there and flies the rest of the way', async () => {
    const onLeave = vi.fn<(resume: MapResume) => void>();
    const first = await started({ showing: found(), onLeave });
    // The flight to Sol has not landed when the page is left.
    expect(first.map.flights).toHaveLength(1);
    first.unmount();
    const resume = onLeave.mock.calls[0][0];
    // Sol was not reached, so it is not handed back as shown.
    expect(resume.shownId).toBeNull();

    const second = await started({ showing: found(), resume });
    expect(second.map.options.startView).toEqual(resume.view);
    expect(second.map.flights).toHaveLength(1);
    expect(second.map.flights[0].target).toMatchObject({
      cursor: [0, 0, 0],
      distance: 70,
    });
    expect(second.map.getSelection()?.name).toBe('Sol');
  });
});

describe('the map’s licence', () => {
  const fromWeb = (path: string) => resolve(process.cwd(), path);
  // pnpm keeps a package's own dependencies beside it.
  const galaxyMap = realpathSync(
    fromWeb('node_modules/@elite-dangerous-almanac/galaxy-map'),
  );
  const SHIPPED: [string, string][] = [
    ['galaxy-map-LICENSE.txt', resolve(galaxyMap, 'LICENSE.md')],
    [
      'galaxy-map-THIRD_PARTY_NOTICES.txt',
      resolve(galaxyMap, 'THIRD_PARTY_NOTICES.md'),
    ],
    ['almanac-core-LICENSE.txt', resolve(galaxyMap, '../core/LICENSE')],
    [
      'almanac-core-THIRD_PARTY_NOTICES.txt',
      resolve(galaxyMap, '../core/THIRD_PARTY_NOTICES.md'),
    ],
    ['gl-matrix-LICENSE.txt', resolve(galaxyMap, '../../gl-matrix/LICENSE.md')],
  ];

  it.each(SHIPPED)(
    'ships %s exactly as the installed package has it',
    (shipped, installed) => {
      expect(readFileSync(fromWeb(`static/licences/${shipped}`), 'utf8')).toBe(
        readFileSync(installed, 'utf8'),
      );
    },
  );

  it('shows every "Required Notice" line of the licence beside the map', async () => {
    // PolyForm Noncommercial 1.0.0 asks that anyone given a copy also gets
    // the terms and each line beginning "Required Notice:". The licence's
    // own worked example is a quoted line ("> Required Notice: …").
    const notices = readFileSync(resolve(galaxyMap, 'LICENSE.md'), 'utf8')
      .split(/\r?\n/u)
      .map((line) => line.trimEnd())
      .filter((line) => line.startsWith('Required Notice:'));
    expect(notices).toEqual([
      'Required Notice: Copyright 2026 Dark Session (https://github.com/Elite-Dangerous-Almanac/Galaxy-Map)',
    ]);
    const { container } = await started();
    const shown = container
      .querySelector('.galaxy-map-notice')!
      .textContent!.replace(/\s+/gu, ' ');
    for (const notice of notices) expect(shown).toContain(notice);
    expect(shown).toContain('property of Frontier Developments plc');
  });

  it('links the terms and the notices, to files the site serves', async () => {
    const { container } = await started();
    const links = [...container.querySelectorAll('.galaxy-map-notice a')]
      .map((link) => link.getAttribute('href') ?? '')
      .filter((href) => href.includes('/licences/'));
    expect(links).toHaveLength(2);
    for (const href of links)
      expect(
        existsSync(fromWeb(`static${href.slice(href.indexOf('/licences/'))}`)),
      ).toBe(true);
  });

  it('shows the notice even where the map cannot start', async () => {
    failNextMap('no graphics card');
    const { container } = render(GalaxyMap, { props: { showing: null } });
    await screen.findByRole('alert');
    expect(
      container.querySelector('.galaxy-map-notice')!.textContent,
    ).toContain('Required Notice: Copyright 2026 Dark Session');
  });
});
