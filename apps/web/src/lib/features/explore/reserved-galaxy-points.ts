import type {
  AutocompleteSystem,
  ExploreSystem,
  WatchlistEntry,
} from '$lib/api/client';
import { SOL_ID64 } from '$lib/api/client';
import type { PinnedEntry } from '$lib/persistence/storage';
import type { GalaxySystemPoint } from '$lib/spatial/contracts';
import { mapExploreSelectedSystemPoint } from '$lib/spatial/explore-scene';

/** Important page context survives sampling, but absent coordinates stay absent. */
export function reservedExploreGalaxyPoints(context: {
  selected: GalaxySystemPoint | null;
  anchor: AutocompleteSystem | null;
  systems: readonly ExploreSystem[];
  pins: readonly PinnedEntry[];
  watched: readonly WatchlistEntry[];
}): readonly GalaxySystemPoint[] {
  const selected = context.selected;
  const position = selected?.positionLy;
  const points: GalaxySystemPoint[] =
    selected &&
    position &&
    [position.x, position.y, position.z].every(Number.isFinite) &&
    (position.x !== 0 ||
      position.y !== 0 ||
      position.z !== 0 ||
      selected.systemId64 === SOL_ID64)
      ? [selected]
      : [];
  const add = (system: ExploreSystem) => {
    const point = mapExploreSelectedSystemPoint(system);
    if (point) points.push(point);
  };
  if (context.anchor) {
    const { id64, name, x, y, z } = context.anchor;
    add({ id64, name, coords: { x, y, z } });
  }
  for (const system of context.systems) add(system);
  for (const pin of context.pins) {
    const raw = pin.coords;
    const coords =
      raw && typeof raw === 'object' && !Array.isArray(raw)
        ? (raw as Record<string, unknown>)
        : pin;
    const coordinate = (value: unknown) =>
      typeof value === 'number' && Number.isFinite(value) ? value : null;
    add({
      id64: pin.id64,
      name: pin.name,
      coords: {
        x: coordinate(coords.x),
        y: coordinate(coords.y),
        z: coordinate(coords.z),
      },
      ...(typeof pin.main_star_type === 'string' && {
        main_star_type: pin.main_star_type,
      }),
    });
  }
  for (const system of context.watched) {
    add({
      id64: system.system_id64,
      name: system.name,
      coords: { x: system.x, y: system.y, z: system.z },
    });
  }
  return points;
}
