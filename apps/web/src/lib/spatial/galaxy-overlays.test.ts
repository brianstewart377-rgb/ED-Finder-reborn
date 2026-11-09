import { describe, expect, it } from 'vitest';

import type { SpatialContribution } from './contracts';
import {
  GALAXY_OVERLAY_ORDER,
  collectGalaxySpatialContributions,
} from './galaxy-overlays';

function contribution(id: string): SpatialContribution {
  return {
    id,
    owner: 'CATALOGUE',
    revision: 1,
    layers: [],
  };
}

describe('collectGalaxySpatialContributions', () => {
  it('preserves the stable product overlay order', () => {
    const result = collectGalaxySpatialContributions({
      regions: contribution('authoritative-galaxy-regions'),
      nebulae: contribution('catalogue:galaxy-nebulae'),
      density: contribution('catalogue-density'),
      catalogueStars: contribution('catalogue-viewport-stars'),
      commanderHistory: contribution('commander-history'),
    });

    expect(result.map((item) => item.id)).toEqual(GALAXY_OVERLAY_ORDER);
  });

  it('drops unavailable overlays without changing the order of the rest', () => {
    const result = collectGalaxySpatialContributions({
      regions: null,
      nebulae: contribution('catalogue:galaxy-nebulae'),
      density: null,
      catalogueStars: null,
      commanderHistory: contribution('commander-history'),
    });

    expect(result.map((item) => item.id)).toEqual([
      'catalogue:galaxy-nebulae',
      'commander-history',
    ]);
  });

  it('returns an empty collection when every overlay is unavailable', () => {
    expect(
      collectGalaxySpatialContributions({
        regions: null,
        nebulae: null,
        density: null,
        catalogueStars: null,
        commanderHistory: null,
      }),
    ).toEqual([]);
  });

  it('orders catalogue-density beneath catalogue stars', () => {
    const order = GALAXY_OVERLAY_ORDER as readonly string[];
    expect(order).toContain('catalogue-density');
    expect(order.indexOf('catalogue-density')).toBeLessThan(
      order.indexOf('catalogue-viewport-stars'),
    );
  });
});
