import { describe, expect, it, vi } from 'vitest';
import { isTopmostPopout, registerPopout } from './popout-stack';

describe('shared pop-out order', () => {
  it('raises a containing map window with its latest child above intervening workspace tools', () => {
    const map = document.createElement('div');
    const filters = document.createElement('div');
    const layers = document.createElement('div');
    map.append(layers);
    const mapLayer = vi.fn();
    const filtersLayer = vi.fn();
    const layersLayer = vi.fn();
    const removeMap = registerPopout(map, mapLayer);
    const removeFilters = registerPopout(filters, filtersLayer);
    const removeLayers = registerPopout(layers, layersLayer);
    try {
      expect(mapLayer).toHaveBeenLastCalledWith(32);
      expect(filtersLayer).toHaveBeenLastCalledWith(31);
      expect(layersLayer).toHaveBeenLastCalledWith(32);
      expect(isTopmostPopout(layers)).toBe(true);
      removeLayers();
      expect(mapLayer).toHaveBeenLastCalledWith(30);
      expect(isTopmostPopout(filters)).toBe(true);
      removeFilters();
      expect(isTopmostPopout(map)).toBe(true);
    } finally {
      removeLayers();
      removeFilters();
      removeMap();
    }
    expect(isTopmostPopout(map)).toBe(false);
  });
});
