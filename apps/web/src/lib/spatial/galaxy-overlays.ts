import type { SpatialContribution } from './contracts';

/**
 * Stable product order for Galaxy overlays.
 *
 * Regions are the cartographic base and must sit before contextual overlays.
 * Finder and Catalogue stars are data layers, Commander History is personal
 * truth, and future public Codex/POI feeds keep their own source identity.
 */
export const GALAXY_OVERLAY_ORDER = [
  'authoritative-galaxy-regions',
  'catalogue:galaxy-nebulae',
  'catalogue-density',
  'catalogue-viewport-stars',
  'commander-history',
] as const;

export type GalaxyOverlayContributionId = (typeof GALAXY_OVERLAY_ORDER)[number];

export type GalaxyOverlayInput = Readonly<{
  regions: SpatialContribution | null;
  nebulae: SpatialContribution | null;
  density: SpatialContribution | null;
  catalogueStars: SpatialContribution | null;
  commanderHistory: SpatialContribution | null;
}>;

const overlayById: Readonly<
  Record<GalaxyOverlayContributionId, keyof GalaxyOverlayInput>
> = {
  'authoritative-galaxy-regions': 'regions',
  'catalogue:galaxy-nebulae': 'nebulae',
  'catalogue-density': 'density',
  'catalogue-viewport-stars': 'catalogueStars',
  'commander-history': 'commanderHistory',
};

export function collectGalaxySpatialContributions(
  input: GalaxyOverlayInput,
): readonly SpatialContribution[] {
  return GALAXY_OVERLAY_ORDER.flatMap((id) => {
    const contribution = input[overlayById[id]];
    return contribution ? [contribution] : [];
  });
}
