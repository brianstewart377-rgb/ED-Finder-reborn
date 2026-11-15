/**
 * A stand-in for Almanac's map in tests, which run with no graphics card.
 *
 * It keeps what it is given and answers the way the real map's handle is
 * documented to (its type declarations, version 0.7.1), and draws nothing.
 * Tests read it to check what the page asked the map to show: which systems
 * in which groups, what is selected, and where the camera was sent.
 *
 * Only tests import this file:
 *
 *   vi.mock('@elite-dangerous-almanac/galaxy-map', () =>
 *     import('$lib/galaxy-map/fake-almanac'));
 */
import type {
  FlightOutcome,
  FlyToTarget,
  GalaxyMapOptions,
  MapView,
  RealSystem,
  SystemDetails,
} from '@elite-dangerous-almanac/galaxy-map';

type Group = {
  name: string;
  color: readonly [number, number, number];
  markerStyle: string;
  visible: boolean;
};
type Sphere = Record<string, unknown>;
type Flight = {
  target: FlyToTarget;
  settle: ((how: FlightOutcome) => void) | null;
};

/** The whole-galaxy view the fake opens on. */
export const FAKE_HOME: MapView = {
  cursor: [0, 0, 25_000],
  distance: 100_000,
  yaw: 0,
  pitch: 50,
};

export class FakeMap {
  readonly groups = new Map<string, Group>();
  readonly systems = new Map<string, RealSystem>();
  spheres: Sphere[] = [];
  /** Every flight asked for, in order. */
  readonly flights: Flight[] = [];
  /** How many times the map was emptied to be filled again. */
  fills = 0;
  /** How many times systems were handed to the map. */
  adds = 0;
  /** Every system the panel was opened on, in order. */
  readonly opened: string[] = [];
  /** What the open panel was told about its system: asked once, on opening. */
  private told: Promise<SystemDetails | null> | null = null;
  private asking: AbortController | null = null;
  disposed = false;
  interaction = {
    zoom: true,
    orbit: true,
    pan: true,
    keys: true,
    select: true,
  };
  readonly hud = {
    refreshed: 0,
    refresh: () => void (this.hud.refreshed += 1),
  };
  readonly ready: Promise<void>;
  private view: MapView;
  private selected: string | null = null;

  constructor(
    readonly canvas: HTMLCanvasElement,
    readonly options: GalaxyMapOptions,
    failure: string | null,
  ) {
    const start = options.startView;
    this.view = {
      cursor: [...(start?.cursor ?? FAKE_HOME.cursor)] as MapView['cursor'],
      distance: start?.distance ?? FAKE_HOME.distance,
      yaw: start?.yaw ?? FAKE_HOME.yaw,
      pitch: start?.pitch ?? FAKE_HOME.pitch,
    };
    Object.assign(this.interaction, options.interaction ?? {});
    this.ready = failure
      ? Promise.reject(new Error(failure))
      : Promise.resolve();
    // A page that never looks at `ready` must not fail the test run.
    this.ready.catch(() => undefined);
  }

  addCategories(groups: readonly Record<string, unknown>[]) {
    for (const group of groups)
      this.groups.set(String(group.name), {
        name: String(group.name),
        color: group.color as Group['color'],
        markerStyle: String(group.markerStyle ?? 'glow'),
        visible: true,
      });
    return { added: groups.length, replaced: 0, rejected: [] };
  }

  addSystems(records: readonly Record<string, unknown>[]) {
    const rejected: { index: number; reason: string }[] = [];
    records.forEach((record, index) => {
      const coords = record.coords as { x: number; y: number; z: number };
      const categories = (record.categories ?? []) as string[];
      if (!record.name) rejected.push({ index, reason: 'no-name' });
      else if (![coords?.x, coords?.y, coords?.z].every(Number.isFinite))
        rejected.push({ index, reason: 'no-coords' });
      else if (categories.some((name) => !this.groups.has(name)))
        rejected.push({ index, reason: 'unknown-category' });
      else {
        const id64 = record.id64 == null ? undefined : String(record.id64);
        this.systems.set(id64 ?? String(record.name), {
          name: String(record.name),
          position: [coords.x, coords.y, coords.z],
          categories,
          id64,
          primaryStar: record.primaryStar as string | undefined,
        });
      }
    });
    this.adds += 1;
    return {
      added: records.length - rejected.length,
      replaced: 0,
      rejected,
    };
  }

  /** A selection of a system that has gone is dropped, as the real map does. */
  clearSystemsAndCategories(): void {
    this.systems.clear();
    this.groups.clear();
    this.spheres = [];
    this.fills += 1;
    this.select(null);
  }

  systemCount(): number {
    return this.systems.size;
  }

  addSpheres(spheres: readonly Sphere[]) {
    this.spheres.push(...spheres);
    return { added: spheres.length, rejected: [] };
  }

  clearShapes(): void {
    this.spheres = [];
  }

  categoryCount(): number {
    return this.groups.size;
  }

  getCategory(index: number) {
    return [...this.groups.values()][index] ?? null;
  }

  isCategoryVisible(name: string): boolean {
    return this.groups.get(name)?.visible ?? false;
  }

  setCategoryVisible(name: string, visible: boolean): void {
    const group = this.groups.get(name);
    if (group) group.visible = visible;
  }

  getSelection(): RealSystem | null {
    return this.selected ? (this.systems.get(this.selected) ?? null) : null;
  }

  /**
   * The panel follows the selection, and asks what to say about a system
   * once, when it opens on it: selecting the same system again asks nothing.
   */
  private select(identity: string | null): void {
    if (identity === this.selected) return;
    this.asking?.abort();
    this.asking = null;
    this.told = null;
    this.selected = identity;
    const system = identity ? this.systems.get(identity) : undefined;
    const hud = this.options.hud;
    if (!identity || !system) return;
    this.opened.push(identity);
    if (typeof hud !== 'object' || !hud.details) return;
    this.asking = new AbortController();
    this.told = Promise.resolve(hud.details(system, this.asking.signal));
  }

  /** Selecting a system ends a flight and brings the camera to the system. */
  setSelection(identity: string | null): void {
    const system = identity ? this.systems.get(identity) : undefined;
    this.select(system ? identity : null);
    if (!system || !identity) return;
    this.endFlight('interrupted');
    this.view = {
      ...this.view,
      cursor: [...system.position] as MapView['cursor'],
      distance: Math.min(this.view.distance, 500),
    };
  }

  getView(): MapView {
    return { ...this.view, cursor: [...this.view.cursor] as MapView['cursor'] };
  }

  setView(view: Partial<MapView>): void {
    this.endFlight('interrupted');
    this.view = { ...this.view, ...view };
  }

  flyTo(target: FlyToTarget): Promise<FlightOutcome> {
    this.endFlight('interrupted');
    return new Promise((settle) => {
      this.flights.push({ target, settle });
    });
  }

  isFlying(): boolean {
    return this.flights.at(-1)?.settle != null;
  }

  setInteraction(next: Partial<typeof this.interaction>): void {
    Object.assign(this.interaction, next);
  }

  dispose(): void {
    this.endFlight('interrupted');
    this.asking?.abort();
    this.disposed = true;
  }

  private endFlight(how: FlightOutcome): void {
    const flight = this.flights.at(-1);
    if (!flight?.settle) return;
    const settle = flight.settle;
    flight.settle = null;
    if (how === 'landed')
      this.view = {
        cursor: [
          ...(flight.target.cursor ?? this.view.cursor),
        ] as MapView['cursor'],
        distance: flight.target.distance ?? this.view.distance,
        yaw: flight.target.yaw ?? this.view.yaw,
        pitch: flight.target.pitch ?? this.view.pitch,
      };
    settle(how);
  }

  // What follows is for tests only: the real map has none of it.

  /** The flight in the air reaches its target. */
  land(): void {
    this.endFlight('landed');
  }

  /** The player drags the map: a flight in the air ends where it is. */
  playerMovesTo(view: Partial<MapView>): void {
    this.setView(view);
  }

  /** The player clicks a marker. */
  playerSelects(identity: string): void {
    this.setSelection(identity);
  }

  /**
   * What the map's panel says about a system, with its buttons. For the
   * selected system this is what the open panel was told when it opened,
   * which may be out of date by now, or still on its way. For any other
   * system it is what the panel would be told if the player opened it now.
   */
  async panel(identity: string): Promise<SystemDetails | null> {
    if (identity === this.selected) return this.told;
    const system = this.systems.get(identity);
    const hud = this.options.hud;
    if (!system || typeof hud !== 'object' || !hud.details) return null;
    return hud.details(system, new AbortController().signal);
  }

  /** Whether the open panel is still waiting to be told about its system. */
  async panelIsWaiting(): Promise<boolean> {
    if (!this.told) return false;
    const waiting = Symbol('waiting');
    const first = await Promise.race([
      this.told,
      new Promise((done) => setTimeout(() => done(waiting), 0)),
    ]);
    return first === waiting;
  }

  /** The names of the systems in a group, in the order they were given. */
  namesIn(group: string): string[] {
    return [...this.systems.values()]
      .filter((system) => system.categories.includes(group))
      .map((system) => system.name);
  }
}

/** Every map made since the last `resetFakeMaps`, oldest first. */
export const fakeMaps: FakeMap[] = [];
let failure: string | null = null;

/** The next map made refuses to start, with this as its reason. */
export function failNextMap(reason: string): void {
  failure = reason;
}

export function resetFakeMaps(): void {
  fakeMaps.length = 0;
  failure = null;
}

export function createGalaxyMap(
  canvas: HTMLCanvasElement,
  options: GalaxyMapOptions = {},
): FakeMap {
  const made = new FakeMap(canvas, options, failure);
  failure = null;
  fakeMaps.push(made);
  return made;
}
