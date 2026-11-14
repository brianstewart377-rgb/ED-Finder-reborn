/**
 * How a body looks on the System Map, from its class alone: its family, the
 * family's colour and name (the same rule as everywhere else in the app,
 * `$lib/spatial/system-scene`), and whether the family is drawn banded.
 */
import {
  BODY_FAMILY_PRESENTATIONS,
  bodyFamilyFromClass,
} from '$lib/spatial/system-scene';

export type FamilyKey = keyof typeof BODY_FAMILY_PRESENTATIONS;
export type Rgb = readonly [number, number, number];

/** A planet class's family, as one key ("gas-giant-II", "icy", "unknown"). */
export function familyKey(bodyClass: string | null): FamilyKey {
  const found = bodyFamilyFromClass(bodyClass);
  if (found.family === 'gas-giant') return `gas-giant-${found.variant}`;
  if (found.family === 'gas-giant-life')
    return `gas-giant-life-${found.variant}`;
  return found.family;
}

/** A family's colour, each part from 0 to 1. */
export function familyColour(key: FamilyKey): Rgb {
  return BODY_FAMILY_PRESENTATIONS[key].colour;
}

/** True for the families drawn with bands, as the app's other maps draw them. */
export function isBanded(key: FamilyKey): boolean {
  return (
    key.startsWith('gas-giant') ||
    key === 'helium-gas-giant' ||
    key === 'water-giant'
  );
}
