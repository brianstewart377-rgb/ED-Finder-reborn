import {
  createPersistedStore,
  PERSISTENCE_KEYS,
  type Codec,
  type PersistedStore,
} from './storage';

export interface MapLayerPreferences {
  density: boolean;
  journeys: boolean;
}

const mapLayerPreferencesCodec: Codec<MapLayerPreferences> = {
  version: 1,
  decode(raw) {
    let value: unknown;
    try {
      value = JSON.parse(raw);
    } catch {
      return {
        ok: false,
        problem: 'corrupt-json',
        detail: 'Stored map layer preferences are not valid JSON',
      };
    }
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return {
        ok: false,
        problem: 'invalid-shape',
        detail: 'Stored map layer preferences have the wrong shape',
      };
    }
    const preferences = value as Record<string, unknown>;
    if (
      typeof preferences.version === 'number' &&
      Number.isInteger(preferences.version) &&
      preferences.version > 1
    ) {
      return {
        ok: false,
        problem: 'unsupported-version',
        detail: 'Stored map layer preferences use a newer version',
      };
    }
    if (
      preferences.version !== 1 ||
      typeof preferences.density !== 'boolean' ||
      typeof preferences.journeys !== 'boolean'
    ) {
      return {
        ok: false,
        problem: 'invalid-shape',
        detail: 'Stored map layer preferences have the wrong shape',
      };
    }
    return {
      ok: true,
      value: {
        density: preferences.density,
        journeys: preferences.journeys,
      },
    };
  },
  encode: ({ density, journeys }) =>
    JSON.stringify({ version: 1, density, journeys }),
};

/** Browser preferences only: journal data and account identity are never stored. */
export function createMapLayerPreferences(): PersistedStore<MapLayerPreferences> {
  return createPersistedStore({
    key: PERSISTENCE_KEYS.mapLayers,
    initial: () => ({ density: true, journeys: false }),
    codec: mapLayerPreferencesCodec,
  });
}
