import { systemDetailFromV3, type BodyV1 } from '$lib/api/client';
import { v3Body, v3Ring, v3System } from '$lib/api/v3-system-fixtures';

const bodyKey = (id: number): string => String(10_000 + id);

/**
 * Synthetic catalogue with Sol's shape: one star, eight planets and 32 moons.
 * Approximate physical measurements exercise wide distance/radius ranges;
 * they are fixture inputs, not an astronomical catalogue or phase ephemeris.
 */
export function solLikeSystemDetail() {
  const bodies: BodyV1[] = [
    v3Body({
      body_id: bodyKey(0),
      frontier_body_id: '0',
      name: 'Sol-like',
    }),
  ];
  const planets = [
    ['Mercury', 58_000_000, 2_440, 'Metal-rich body'],
    ['Venus', 108_000_000, 6_052, 'High metal content world'],
    ['Earth', 150_000_000, 6_371, 'Earth-like world'],
    ['Mars', 228_000_000, 3_390, 'Rocky body'],
    ['Jupiter', 778_000_000, 69_911, 'Class I gas giant'],
    ['Saturn', 1_434_000_000, 58_232, 'Class I gas giant'],
    ['Uranus', 2_871_000_000, 25_362, 'Class II gas giant'],
    ['Neptune', 4_495_000_000, 24_622, 'Class III gas giant'],
  ] as const;
  for (const [
    index,
    [name, axisKm, radiusKm, bodyClass],
  ] of planets.entries()) {
    bodies.push(
      v3Body({
        body_id: bodyKey(index + 1),
        frontier_body_id: String(index + 1),
        direct_parent_body_id: bodyKey(0),
        name,
        body_type: 'planet',
        body_class: bodyClass,
        is_main_star: false,
        spectral_class: null,
        luminosity_class: null,
        radius_km: radiusKm,
        stellar_mass_solar: null,
        semi_major_axis_km: axisKm,
        distance_from_arrival_ls: axisKm / 299_792.458,
      }),
    );
  }
  // Earth 1, Mars 2, Jupiter 8, Saturn 10, Uranus 6, Neptune 5 = 32.
  const moonGroups = [
    [3, [384_400]],
    [4, [9_376, 23_463]],
    [
      5,
      [
        128_000, 129_000, 181_400, 221_900, 421_700, 671_100, 1_070_400,
        1_882_700,
      ],
    ],
    [
      6,
      [
        133_600, 137_700, 139_400, 141_700, 151_500, 185_500, 238_000, 294_600,
        377_400, 1_221_900,
      ],
    ],
    [7, [64_400, 129_900, 190_900, 266_000, 436_300, 583_500]],
    [8, [48_200, 50_100, 52_500, 117_600, 354_800]],
  ] as const;
  let moonId = 100;
  for (const [parentId, axesKm] of moonGroups) {
    for (const [index, axisKm] of axesKm.entries()) {
      bodies.push(
        v3Body({
          body_id: bodyKey(moonId),
          frontier_body_id: String(moonId++),
          direct_parent_body_id: bodyKey(parentId),
          name: `${planets[parentId - 1]![0]} moon ${index + 1}`,
          body_type: 'planet',
          body_class: index % 2 === 0 ? 'Rocky body' : 'Icy body',
          is_main_star: false,
          spectral_class: null,
          luminosity_class: null,
          radius_km: 100 + index * 200,
          stellar_mass_solar: null,
          semi_major_axis_km: axisKm,
          distance_from_arrival_ls: planets[parentId - 1]![1] / 299_792.458,
        }),
      );
    }
  }
  return systemDetailFromV3(
    v3System({
      name: 'Sol-like',
      loaded_body_count: 41,
      source_body_count: 41,
    }),
    bodies,
    [
      v3Ring({ body_id: bodyKey(6), name: 'Saturn A Ring' }),
      v3Ring({
        body_id: bodyKey(7),
        ring_id: '900719925632971230',
        name: 'Uranus Ring',
        inner_radius_km: 38_000,
        outer_radius_km: 52_000,
      }),
    ],
    [],
  );
}
