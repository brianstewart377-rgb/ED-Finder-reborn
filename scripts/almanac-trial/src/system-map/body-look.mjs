// How a planet looks by its class: which family it is in, and that family's
// colour and name.
//
// This is a COPY of our own map's rule, made by program on 13 Nov 2026 from
// apps/web/src/lib/spatial/system-scene.ts (bodyFamilyFromClass and
// BODY_FAMILY_PRESENTATIONS). The web app's file cannot be read outside its
// own build, so the trial keeps a copy; trial.test.mjs reads that file as
// text and fails if the two differ. If the trial passes, the map is built in
// apps/web and uses the original.

/** A planet class as our data names it, in small letters, and its family. */
export const FAMILY_OF_CLASS = {
  'earth-like world': 'earth-like',
  'water world': 'water-world',
  'ammonia world': 'ammonia-world',
  'high metal content world': 'high-metal-content',
  'metal-rich body': 'metal-rich',
  'rocky body': 'rocky',
  'rocky ice world': 'rocky-ice',
  'rocky ice body': 'rocky-ice',
  'icy body': 'icy',
  'class i gas giant': 'gas-giant-I',
  'class ii gas giant': 'gas-giant-II',
  'class iii gas giant': 'gas-giant-III',
  'class iv gas giant': 'gas-giant-IV',
  'class v gas giant': 'gas-giant-V',
  'gas giant with water-based life': 'gas-giant-life-water',
  'gas giant with water based life': 'gas-giant-life-water',
  'gas giant with ammonia-based life': 'gas-giant-life-ammonia',
  'gas giant with ammonia based life': 'gas-giant-life-ammonia',
  'helium-rich gas giant': 'helium-gas-giant',
  'helium gas giant': 'helium-gas-giant',
  'water giant': 'water-giant',
};

/** Each family's name and colour. Each part of a colour runs from 0 to 1. */
export const FAMILIES = {
  'earth-like': { label: 'Earth-like', colour: [0.16, 0.58, 0.57] },
  'water-world': { label: 'Water world', colour: [0.12, 0.32, 0.64] },
  'ammonia-world': { label: 'Ammonia world', colour: [0.62, 0.39, 0.22] },
  'high-metal-content': { label: 'High metal content', colour: [0.52, 0.46, 0.4] },
  'metal-rich': { label: 'Metal-rich', colour: [0.34, 0.3, 0.28] },
  'rocky': { label: 'Rocky', colour: [0.72, 0.57, 0.4] },
  'rocky-ice': { label: 'Rocky ice', colour: [0.62, 0.7, 0.76] },
  'icy': { label: 'Icy', colour: [0.82, 0.92, 0.97] },
  'gas-giant-I': { label: 'Gas giant · I', colour: [0.8, 0.78, 0.67] },
  'gas-giant-II': { label: 'Gas giant · II', colour: [0.88, 0.76, 0.53] },
  'gas-giant-III': { label: 'Gas giant · III', colour: [0.42, 0.56, 0.68] },
  'gas-giant-IV': { label: 'Gas giant · IV', colour: [0.67, 0.35, 0.24] },
  'gas-giant-V': { label: 'Gas giant · V', colour: [0.46, 0.15, 0.13] },
  'gas-giant-life-water': { label: 'Life-bearing giant · water', colour: [0.41, 0.63, 0.46] },
  'gas-giant-life-ammonia': { label: 'Life-bearing giant · ammonia', colour: [0.61, 0.45, 0.68] },
  'helium-gas-giant': { label: 'Helium gas giant', colour: [0.88, 0.84, 0.75] },
  'water-giant': { label: 'Water giant', colour: [0.64, 0.83, 0.94] },
  'unknown': { label: 'Unknown class', colour: [0.48, 0.48, 0.52] },
};

/** The family of a planet class. A class the rule does not know is "unknown". */
export function familyOf(bodyClass) {
  return FAMILY_OF_CLASS[String(bodyClass ?? '').trim().toLowerCase()] ?? 'unknown';
}

/** True for the families the map draws with bands, as our own map does. */
export function isBanded(family) {
  return family.startsWith('gas-giant') || family === 'helium-gas-giant' || family === 'water-giant';
}
