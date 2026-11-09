import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createMapLayerPreferences } from './map-layers';
import { PROFILE_SYNC_KEYS } from './profile-sync';
import { PERSISTENCE_KEYS } from './storage';

describe('map layer preferences', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('defaults density on and journeys off without creating a stored value', () => {
    const preferences = createMapLayerPreferences();
    preferences.hydrate();
    expect(get(preferences)).toEqual({
      value: { density: true, journeys: false },
      hydrated: true,
      diagnostic: null,
    });
    expect(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)).toBeNull();
  });

  it('round-trips both switches per browser without account or journal data', () => {
    const preferences = createMapLayerPreferences();
    expect(preferences.set({ density: false, journeys: true })).toBe(true);
    expect(
      JSON.parse(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)!),
    ).toEqual({
      version: 1,
      density: false,
      journeys: true,
    });
    const restored = createMapLayerPreferences();
    restored.hydrate();
    expect(get(restored).value).toEqual({ density: false, journeys: true });
    expect(PROFILE_SYNC_KEYS).not.toContain(PERSISTENCE_KEYS.mapLayers);
    expect(restored.set({ density: true, journeys: false })).toBe(true);
    expect(createMapLayerPreferences().read().value).toEqual({
      density: true,
      journeys: false,
    });
  });

  it.each([
    '{broken',
    'null',
    '[]',
    '{}',
    '{"version":1,"density":"false","journeys":true}',
    '{"version":1,"density":false,"journeys":1}',
    '{"version":1,"density":false}',
    '{"version":0,"density":false,"journeys":true}',
  ])('recovers invalid stored preferences using defaults: %s', (raw) => {
    localStorage.setItem(PERSISTENCE_KEYS.mapLayers, raw);
    const preferences = createMapLayerPreferences();
    preferences.hydrate();
    expect(get(preferences).value).toEqual({ density: true, journeys: false });
    expect(get(preferences).diagnostic?.problem).toBe(
      raw === '{broken' ? 'corrupt-json' : 'invalid-shape',
    );
    expect(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)).toBe(raw);
  });

  it('preserves preferences belonging to a newer application version', () => {
    const raw = '{"version":2,"density":false,"journeys":true}';
    localStorage.setItem(PERSISTENCE_KEYS.mapLayers, raw);
    const preferences = createMapLayerPreferences();
    preferences.hydrate();
    expect(get(preferences).diagnostic?.problem).toBe('unsupported-version');
    expect(preferences.set({ density: false, journeys: false })).toBe(false);
    expect(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)).toBe(raw);
  });

  it('only decodes and encodes the two switch positions', () => {
    const preferences = createMapLayerPreferences();
    expect(
      preferences.setFromJsonValue({
        version: 1,
        density: false,
        journeys: true,
        journals: [{ system: 'private' }],
        account: 'private',
      }),
    ).toBe(true);
    expect(
      JSON.parse(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)!),
    ).toEqual({
      version: 1,
      density: false,
      journeys: true,
    });
  });

  it.each(['getter', 'getItem', 'setItem'] as const)(
    'keeps switches usable when localStorage %s throws',
    (failure) => {
      const unavailable = () => {
        throw new DOMException('Storage blocked', 'SecurityError');
      };
      if (failure === 'getter') {
        vi.spyOn(window, 'localStorage', 'get').mockImplementation(unavailable);
      } else {
        vi.spyOn(Storage.prototype, failure).mockImplementation(unavailable);
      }
      const preferences = createMapLayerPreferences();
      expect(() => preferences.hydrate()).not.toThrow();
      expect(get(preferences).value).toEqual({
        density: true,
        journeys: false,
      });
      expect(preferences.set({ density: false, journeys: true })).toBe(false);
      expect(get(preferences)).toMatchObject({
        value: { density: false, journeys: true },
        hydrated: true,
        diagnostic: { problem: 'unavailable' },
      });
    },
  );

  it('can be constructed and hydrated without a browser', () => {
    vi.stubGlobal('window', undefined);
    const preferences = createMapLayerPreferences();
    expect(() => preferences.hydrate()).not.toThrow();
    expect(get(preferences)).toEqual({
      value: { density: true, journeys: false },
      hydrated: true,
      diagnostic: null,
    });
  });
});
