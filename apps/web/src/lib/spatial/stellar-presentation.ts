/**
 * Renderer-neutral stellar icon families for the Galaxy map.
 *
 * The catalogue values remain the truth. These descriptors only control the
 * non-factual marker shape, colour and relative display size.
 */
export type StellarGlyph =
  | 'ttauri-herbig'
  | 'brown-dwarf-y-t'
  | 'brown-dwarf-l'
  | 'main-sequence-m'
  | 'main-sequence-k'
  | 'main-sequence-g'
  | 'main-sequence-f'
  | 'main-sequence-a'
  | 'main-sequence-b'
  | 'main-sequence-o'
  | 'giant-warm'
  | 'giant-yellow'
  | 'giant-white'
  | 'giant-blue'
  | 'wolf-rayet'
  | 'hypergiant'
  | 'carbon'
  | 'white-dwarf'
  | 'neutron'
  | 'black-hole'
  | 'supermassive-black-hole'
  | 'unknown';

export type StellarPresentation = Readonly<{
  glyph: StellarGlyph;
  label: string;
  coreRgb: readonly [number, number, number];
  haloRgb: readonly [number, number, number];
  relativeScale: number;
  darkCore: boolean;
  ring: boolean;
}>;

type StellarPresentationInput = Readonly<{
  type?: string | null;
  subtype?: string | null;
}>;

const presentations = {
  'ttauri-herbig': [
    'T Tauri / AeBe Herbig',
    [1, 0.09, 0.13],
    [1, 0.16, 0.1],
    0.92,
    false,
    false,
  ],
  'brown-dwarf-y-t': [
    'Y / T brown dwarf',
    [0.78, 0.035, 0.08],
    [0.98, 0.06, 0.12],
    0.72,
    false,
    false,
  ],
  'brown-dwarf-l': [
    'L brown dwarf',
    [1, 0.16, 0.035],
    [1, 0.25, 0.06],
    0.8,
    false,
    false,
  ],
  'main-sequence-m': [
    'M class star',
    [1, 0.2, 0.06],
    [1, 0.26, 0.08],
    0.9,
    false,
    false,
  ],
  'main-sequence-k': [
    'K class star',
    [1, 0.47, 0.19],
    [1, 0.36, 0.11],
    0.96,
    false,
    false,
  ],
  'main-sequence-g': [
    'G class star',
    [1, 0.98, 0.66],
    [1, 0.92, 0.38],
    1,
    false,
    false,
  ],
  'main-sequence-f': [
    'F class star',
    [0.92, 0.94, 1],
    [0.72, 0.76, 1],
    1.04,
    false,
    false,
  ],
  'main-sequence-a': [
    'A class star',
    [0.62, 0.55, 1],
    [0.42, 0.28, 1],
    1.12,
    false,
    false,
  ],
  'main-sequence-b': [
    'B class star',
    [0.29, 0.34, 1],
    [0.09, 0.16, 1],
    1.2,
    false,
    false,
  ],
  'main-sequence-o': [
    'O class star',
    [0.18, 0.27, 1],
    [0.04, 0.12, 1],
    1.3,
    false,
    false,
  ],
  'giant-warm': [
    'M / K giant or supergiant',
    [1, 0.34, 0.12],
    [1, 0.18, 0.04],
    1.72,
    false,
    false,
  ],
  'giant-yellow': [
    'G giant or supergiant',
    [1, 1, 0.62],
    [0.95, 0.9, 0.32],
    1.62,
    false,
    false,
  ],
  'giant-white': [
    'F giant or supergiant',
    [0.9, 0.92, 1],
    [0.65, 0.67, 1],
    1.66,
    false,
    false,
  ],
  'giant-blue': [
    'O / B / A giant or supergiant',
    [0.46, 0.38, 1],
    [0.12, 0.16, 1],
    1.84,
    false,
    false,
  ],
  'wolf-rayet': [
    'Wolf-Rayet star',
    [1, 1, 1],
    [0.7, 0.78, 1],
    1.38,
    false,
    false,
  ],
  hypergiant: [
    'Hypergiant',
    [0.75, 0.8, 1],
    [0.46, 0.52, 1],
    2.1,
    false,
    false,
  ],
  carbon: [
    'Carbon star',
    [0.92, 0.93, 0.42],
    [0.78, 0.63, 0.18],
    1.36,
    false,
    false,
  ],
  'white-dwarf': [
    'White dwarf',
    [0.86, 0.91, 1],
    [0.45, 0.58, 1],
    0.66,
    false,
    false,
  ],
  neutron: ['Neutron star', [1, 1, 1], [0.58, 0.72, 1], 0.52, false, false],
  'black-hole': [
    'Black hole',
    [0.005, 0.007, 0.012],
    [0.56, 0.43, 0.34],
    0.92,
    true,
    true,
  ],
  'supermassive-black-hole': [
    'Supermassive black hole',
    [0, 0, 0],
    [0.55, 0.43, 0.36],
    1.48,
    true,
    true,
  ],
  unknown: [
    'Unknown star class',
    [0.47, 0.78, 0.88],
    [0.25, 0.64, 0.76],
    0.9,
    false,
    false,
  ],
} as const satisfies Record<
  StellarGlyph,
  readonly [
    label: string,
    coreRgb: readonly [number, number, number],
    haloRgb: readonly [number, number, number],
    relativeScale: number,
    darkCore: boolean,
    ring: boolean,
  ]
>;

function descriptor(glyph: StellarGlyph): StellarPresentation {
  const [label, coreRgb, haloRgb, relativeScale, darkCore, ring] =
    presentations[glyph];
  return { glyph, label, coreRgb, haloRgb, relativeScale, darkCore, ring };
}

const spectralGlyphs = {
  O: 'main-sequence-o',
  B: 'main-sequence-b',
  A: 'main-sequence-a',
  F: 'main-sequence-f',
  G: 'main-sequence-g',
  K: 'main-sequence-k',
  M: 'main-sequence-m',
  L: 'brown-dwarf-l',
  T: 'brown-dwarf-y-t',
  Y: 'brown-dwarf-y-t',
} as const satisfies Record<string, StellarGlyph>;

type StellarFamily =
  | keyof typeof spectralGlyphs
  | 'TTS'
  | 'AeBe'
  | 'white-dwarf'
  | 'wolf-rayet'
  | 'carbon'
  | 'neutron'
  | 'black-hole'
  | 'supermassive-black-hole'
  | 'hypergiant'
  | 'unknown';

/**
 * Shared, presentation-only family for raw catalogue classes and the existing
 * descriptive System Map phrases. Match complete tokens, never a word's first
 * letter. The caller retains the original class as the catalogue fact.
 */
export function stellarFamilyFromCatalogue(
  spectralClass: string | null | undefined,
): StellarFamily {
  const value = (spectralClass ?? '')
    .trim()
    .toUpperCase()
    .replace(/[_/-]+/gu, ' ')
    .replace(/\s+/gu, ' ')
    .replace(/(?:^| )STAR$/u, '')
    .trim();
  if (!value) return 'unknown';

  // Subclasses and luminosity are suffixes, not part of the family token.
  // Luminosity classes carry optional sub-letters in the catalogue
  // (`Vab`, `IVb`, `IIIab`, `IIa`, `Ia0`, `Iab`).
  const catalogue = value.match(
    /^([A-Z]+)(?:\d+(?:\.\d+)?)?(?: (?:IA0|VII|VI|IV|III|II|I|V)(?:AB|A|B|Z)?)?$/u,
  );
  const token = catalogue?.[1] ?? '';
  if (Object.hasOwn(spectralGlyphs, token))
    return token as keyof typeof spectralGlyphs;
  if (
    /^(?:D|DA|DAB|DAO|DAZ|DAV|DB|DBZ|DBV|DC|DCV|DO|DOV|DQ|DV|DZ|DX)$/u.test(
      token,
    )
  )
    return 'white-dwarf';
  if (/^(?:W|WC|WN|WNC|WO)$/u.test(token)) return 'wolf-rayet';
  if (/^(?:C|CN|CJ|CH|CHD|CS|MS|S)$/u.test(token)) return 'carbon';
  if (token === 'N') return 'neutron';
  if (token === 'H') return 'black-hole';
  if (token === 'TTS') return 'TTS';
  if (token === 'AEBE') return 'AeBe';

  if (
    /^(?:SUPERMASSIVE(?: BLACK ?HOLE)?|SUPERMASSIVEBLACKHOLE|SAGITTARIUS\s*A\*|SGR\s*A\*)$/u.test(
      value,
    )
  )
    return 'supermassive-black-hole';
  if (/^BLACK ?HOLE$/u.test(value)) return 'black-hole';
  if (value === 'NEUTRON') return 'neutron';
  if (value === 'HYPERGIANT') return 'hypergiant';
  if (/^WHITE ?DWARF$/u.test(value)) return 'white-dwarf';
  if (/^WOLF ?RAYET(?: (?:W?C|W?N|W?NC|W?O))?$/u.test(value))
    return 'wolf-rayet';
  if (/^T ?TAURI$/u.test(value)) return 'TTS';
  if (/^(?:HERBIG(?: AE ?BE)?|AEBE)$/u.test(value)) return 'AeBe';
  if (/^(?:CARBON|MS TYPE|S TYPE)$/u.test(value)) return 'carbon';

  // Preserve phrases such as `D Hypergiant` and `K Giant` without accepting
  // arbitrary text after a valid catalogue prefix.
  const giant = value.match(/^(.+) (HYPERGIANT|SUPERGIANT|GIANT)$/u);
  if (giant) {
    const family = stellarFamilyFromCatalogue(giant[1]);
    if (family !== 'unknown' && giant[2] === 'HYPERGIANT') {
      if (['neutron', 'black-hole', 'supermassive-black-hole'].includes(family))
        return family;
      return 'hypergiant';
    }
    return family;
  }

  // System Map phrases may wrap an already descriptive class. Each recursive
  // call consumes a smaller complete phrase; both sides must agree on family.
  const carbonPhrase = value.match(/^(.+) \(CARBON\)$/u);
  if (carbonPhrase)
    return stellarFamilyFromCatalogue(carbonPhrase[1]) === 'carbon'
      ? 'carbon'
      : 'unknown';
  const phrase = value.match(/^([^()]+?)\s*\((.+)\)$/u);
  if (phrase) {
    const family = stellarFamilyFromCatalogue(phrase[1]);
    if (family === 'unknown') return 'unknown';
    const annotation = phrase[2]!;
    if (stellarFamilyFromCatalogue(annotation) === family) return family;
    if (
      Object.hasOwn(spectralGlyphs, family) &&
      /^(?:BLUE WHITE|WHITE YELLOW|YELLOW ORANGE|RED|BROWN|WHITE|BLUE)(?: (?:DWARF|GIANT|SUPER ?GIANT))?$/u.test(
        annotation,
      )
    )
      return family;
  }
  return 'unknown';
}

export function stellarPresentation(
  input: StellarPresentationInput = {},
): StellarPresentation {
  const typeFamily = stellarFamilyFromCatalogue(input.type);
  const subtypeFamily = stellarFamilyFromCatalogue(input.subtype);
  // Keep the existing remnant/special-class precedence for split-field callers.
  const specialFamily = (
    [
      'supermassive-black-hole',
      'black-hole',
      'neutron',
      'hypergiant',
      'white-dwarf',
      'wolf-rayet',
      'TTS',
      'AeBe',
      'carbon',
    ] as const
  ).find(
    (candidate) => candidate === typeFamily || candidate === subtypeFamily,
  );
  const family =
    specialFamily ?? (typeFamily === 'unknown' ? subtypeFamily : typeFamily);
  if (family === 'TTS' || family === 'AeBe') return descriptor('ttauri-herbig');
  if (!(family in spectralGlyphs)) return descriptor(family as StellarGlyph);

  const spectralClass = family as keyof typeof spectralGlyphs;
  const value = `${input.type ?? ''} ${input.subtype ?? ''}`
    .toUpperCase()
    .replace(/[()/_-]+/gu, ' ');
  const giant =
    /(?:^|\s)(?:(?:IA0|IV|III|II|I)(?:AB|A|B|Z)?|GIANT|SUPERGIANT)(?:\s|$)/u.test(
      value,
    );
  if (giant) {
    if (spectralClass === 'M' || spectralClass === 'K')
      return descriptor('giant-warm');
    if (spectralClass === 'G') return descriptor('giant-yellow');
    if (spectralClass === 'F') return descriptor('giant-white');
    if (['O', 'B', 'A'].includes(spectralClass))
      return descriptor('giant-blue');
  }

  return descriptor(spectralGlyphs[spectralClass]);
}
