/**
 * Test fixtures in the exact V3 canonical wire shapes
 * (`/api/v1/systems/{id}`, `/bodies`, `/rings`, `/stations`). Inspect tests
 * build their `SystemDetail` through the production mapping so they exercise
 * V3 shapes rather than a hand-written domain object.
 */
import {
  RATING_ECONOMIES,
  systemDetailFromV3,
  type BodyV1,
  type EconomyRating,
  type RatingsIdentity,
  type RingV1,
  type StationV1,
  type SystemDetail,
  type SystemRatingExplanation,
  type SystemRatings,
  type SystemV1,
} from './client';

export const v3System = (overrides: Partial<SystemV1> = {}): SystemV1 => ({
  id64: '10477373803',
  name: 'Reference System',
  x_ly: 1,
  y_ly: 2,
  z_ly: 3,
  source_body_count: 3,
  loaded_body_count: 3,
  lifecycle_state: 'ACTIVE',
  source_updated_at: '2026-08-24T21:24:56+00:00',
  ...overrides,
});

export const v3Body = (overrides: Partial<BodyV1> = {}): BodyV1 => ({
  body_id: '36028797018963968',
  source_body_id64: '36028797018963968',
  frontier_body_id: '0',
  direct_parent_body_id: null,
  name: 'Reference System',
  body_type: 'star',
  body_class: null,
  body_class_source: null,
  is_main_star: true,
  spectral_class: 'G2',
  luminosity_class: 'V',
  is_landable: null,
  radius_km: 695_700,
  planet_mass_earth: null,
  stellar_mass_solar: 1,
  surface_pressure_atm: null,
  semi_major_axis_km: null,
  distance_from_arrival_ls: 0,
  lifecycle_state: 'ACTIVE',
  signals: [],
  ...overrides,
});

export const v3Ring = (overrides: Partial<RingV1> = {}): RingV1 => ({
  ring_id: '900719925632971229',
  body_id: '108086391056891904',
  source_ring_id64: '900719925632971229',
  kind: 'RING',
  name: 'Reference System 3 A Ring',
  ring_type: 'icy',
  reserve_type: 'pristine',
  inner_radius_km: 90_000,
  outer_radius_km: 130_000,
  mass_mt: 1_000_000,
  signals_complete: true,
  source_updated_at: '2026-08-24T21:24:56+00:00',
  lifecycle_state: 'ACTIVE',
  signals: [],
  ...overrides,
});

export const v3Station = (overrides: Partial<StationV1> = {}): StationV1 => ({
  station_id: '128000000',
  market_id: '128000000',
  name: 'Reference Port',
  station_type: 'coriolis',
  distance_from_arrival_ls: 500,
  body_id: null,
  association_state: 'UNRESOLVED',
  lifecycle_state: 'ACTIVE',
  economies: [],
  ...overrides,
});

/** A small three-body system: a G2 V main star, a ringed planet, a moon. */
export function referenceSystemDetail(
  overrides: Partial<SystemV1> = {},
): SystemDetail {
  return systemDetailFromV3(
    v3System(overrides),
    [
      v3Body({
        body_id: '108086391056891904',
        source_body_id64: '108086391056891904',
        frontier_body_id: '3',
        direct_parent_body_id: '36028797018963968',
        name: 'Reference System 3',
        body_type: 'planet',
        is_main_star: null,
        spectral_class: null,
        luminosity_class: null,
        is_landable: false,
        radius_km: 70_000,
        stellar_mass_solar: null,
        planet_mass_earth: 300,
        semi_major_axis_km: 3_400_000_000,
        distance_from_arrival_ls: 11_450,
        signals: [
          { signal_type: 'saa_signaltype_biological', count: 2 },
          { signal_type: 'saa_signaltype_geological', count: 1 },
        ],
      }),
      v3Body(),
      v3Body({
        body_id: '180143985094819840',
        source_body_id64: null,
        frontier_body_id: null,
        direct_parent_body_id: '108086391056891904',
        name: 'Reference System 3 a',
        body_type: 'planet',
        is_main_star: null,
        spectral_class: null,
        luminosity_class: null,
        is_landable: true,
        radius_km: 1_200,
        stellar_mass_solar: null,
        semi_major_axis_km: 600_000,
        distance_from_arrival_ls: 11_452,
      }),
    ],
    [
      v3Ring(),
      v3Ring({
        ring_id: '900719925632971230',
        kind: 'BELT',
        name: 'Reference System A Belt',
        body_id: '36028797018963968',
      }),
    ],
    [v3Station()],
  );
}

/** The published Ratings V4 generation the rating fixtures come from. */
export const ratingsIdentity = (
  overrides: Partial<RatingsIdentity> = {},
): RatingsIdentity => ({
  derivedGenerationId: '8f9a7e3c-1d2b-4c5d-9e6f-0a1b2c3d4e5f',
  publicationSequence: '1',
  scorerVersion: '4.0.0',
  mechanicsVersion: 'v4-mechanics-2026-09',
  ...overrides,
});

const REFERENCE_POTENTIALS: Readonly<Record<string, number>> = {
  Agriculture: 84,
  Refinery: 0,
  Industrial: 40,
  HighTech: 62,
  Military: 0,
  Tourism: 70,
  Extraction: 55,
};

function referenceRating(economy: string): EconomyRating {
  const unresolved = economy === 'Refinery';
  return {
    economy,
    potential: REFERENCE_POTENTIALS[economy] ?? 0,
    specialisation: unresolved ? null : 60,
    specialisationMin: unresolved ? 0 : 60,
    specialisationMax: unresolved ? 40 : 60,
    completeness: 0.9,
    confidence: 0.72,
  };
}

/** All seven Ratings V4 economies for the reference system, scorer order. */
export function referenceSystemRatings(
  identity: RatingsIdentity = ratingsIdentity(),
): SystemRatings {
  return { identity, ratings: RATING_ECONOMIES.map(referenceRating) };
}

/**
 * The replayed explanation for {@link referenceSystemRatings}. Agriculture 84
 * is 82% of the ringed planet's 95 (a native water world with two strong
 * links) plus 11% of the moon's 55 (a modifier with one link each way and an
 * unknown reserve level).
 */
export function referenceRatingExplanation(
  identity: RatingsIdentity = ratingsIdentity(),
): SystemRatingExplanation {
  return {
    kind: 'explained',
    identity,
    economies: RATING_ECONOMIES.map((economy) => ({
      ...referenceRating(economy),
      contributions:
        economy === 'Agriculture'
          ? [
              {
                bodyKey: '108086391056891904',
                native: true,
                modifier: false,
                base: 75,
                positive: 20,
                negative: 0,
                local: 95,
                weight: 0.82,
                positiveRules: [
                  'biologicals-agriculture',
                  'water-world-agriculture',
                ],
                negativeRules: [],
                contributors: ['native:water world'],
                unknownEvidence: [],
              },
              {
                bodyKey: '180143985094819840',
                native: false,
                modifier: true,
                base: 55,
                positive: 10,
                negative: 10,
                local: 55,
                weight: 0.11,
                positiveRules: ['biologicals-agriculture'],
                negativeRules: ['icy-agriculture'],
                contributors: ['modifier:agriculture'],
                unknownEvidence: ['reserve_level'],
              },
            ]
          : [],
    })),
  };
}
