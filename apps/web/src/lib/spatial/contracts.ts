/**
 * What the System Map's model is made of (`system-scene.ts`): bodies, their
 * orbits and rings, and what is built on them, each fact with where it came
 * from. No drawing code is named here.
 */
export type RepresentationClass =
  'AUTHORITATIVE' | 'DERIVED' | 'PLANNED' | 'SCHEMATIC' | 'AMBIENT';

export type Provenance = Readonly<{
  source: string;
  observedAt?: string;
  ruleVersion?: string;
  confidence?: string;
  note?: string;
}>;

export type Truth<T> = Readonly<{
  value: T;
  representation: RepresentationClass;
  provenance?: readonly Provenance[];
}>;

export type BodyRef = Readonly<{ systemId64: string; bodyId: number }>;

export type FacilityRef = Readonly<{
  owner: 'EDFINDER' | 'CRE' | 'CPE';
  facilityId: string;
  systemId64: string;
  body?: BodyRef;
}>;

export type SpatialTarget =
  | Readonly<{ kind: 'system'; systemId64: string; name?: string }>
  | Readonly<{ kind: 'body'; ref: BodyRef }>
  | Readonly<{ kind: 'facility'; ref: FacilityRef }>
  | Readonly<{ kind: 'region' | 'route' | 'cluster'; id: string }>;

export type SystemCameraState = Readonly<{
  systemId64: string;
  focus: SpatialTarget;
  semanticDistance: number;
  bearingRad: number;
  pitchRad: number;
  revision: number;
}>;

export type LayerContract<TPayload = unknown> = Readonly<{
  id: string;
  version: number;
  representation: RepresentationClass;
  payload: TPayload;
  bounds?: unknown;
  targetCount: number;
  truncated: boolean;
}>;

export type SpatialContribution = Readonly<{
  id: string;
  owner:
    | 'CATALOGUE'
    | 'SPATIAL_PLATFORM'
    | 'FINDER'
    | 'CRE'
    | 'CPE'
    | 'COMMANDER_HISTORY'
    | 'POWERPLAY'
    | 'ROUTES';
  revision: number;
  layers: readonly LayerContract[];
}>;

export type OrbitalDescriptor = Readonly<{
  periodDays?: Truth<number>;
  semiMajorAxisKm?: Truth<number>;
  semiMajorAxisAu?: Truth<number>;
  eccentricity?: Truth<number>;
  inclinationDeg?: Truth<number>;
  ascendingNodeDeg?: Truth<number>;
  argumentOfPeriapsisDeg?: Truth<number>;
  meanAnomalyDeg?: Truth<number>;
  epoch?: Truth<string>;
  placement: 'OBSERVED_PHASE' | 'COMPUTED_PHASE' | 'DETERMINISTIC_SCHEMATIC';
}>;

export type RingDescriptor = Readonly<{
  state: 'PRESENT' | 'ABSENT' | 'UNKNOWN';
  bands: ReadonlyArray<
    Readonly<{
      id: string;
      ringClass?: Truth<string>;
      innerRadiusM?: Truth<number>;
      outerRadiusM?: Truth<number>;
    }>
  >;
}>;

/** Presentation derived only from a catalogued planet class; never a new fact. */
export type BodyFamily =
  | Readonly<{
      family:
        | 'earth-like'
        | 'water-world'
        | 'ammonia-world'
        | 'high-metal-content'
        | 'metal-rich'
        | 'rocky'
        | 'rocky-ice'
        | 'icy'
        | 'helium-gas-giant'
        | 'water-giant'
        | 'unknown';
    }>
  | Readonly<{ family: 'gas-giant'; variant: 'I' | 'II' | 'III' | 'IV' | 'V' }>
  | Readonly<{ family: 'gas-giant-life'; variant: 'water' | 'ammonia' }>;

export type BodyVisualDescriptor = Readonly<{
  ref: BodyRef;
  name: string;
  /** Canonical keys resolve hierarchy without changing the picking identity. */
  bodyKey?: string;
  directParentBodyKey?: string | null;
  isMainStar?: boolean;
  parent?: BodyRef;
  class?: Truth<string>;
  subtype?: Truth<string>;
  bodyClass?: Truth<string>;
  bodyFamily?: BodyFamily;
  physicalRadiusM?: Truth<number>;
  distanceFromArrivalLs?: Truth<number>;
  displayRadius: number;
  orbital?: OrbitalDescriptor;
  rings?: RingDescriptor;
}>;

export type InfrastructureAttachment = Readonly<{
  facility: FacilityRef;
  body?: BodyRef;
  lane?: Truth<'orbital' | 'surface'>;
  association: 'CONFIRMED' | 'UNRESOLVED' | 'CONFLICT';
}>;

export type SystemSceneContract = Readonly<{
  kind: 'system';
  revision: number;
  systemId64: string;
  fidelity: 'S0' | 'S1' | 'S2' | 'S3' | 'S4' | 'S5';
  camera: SystemCameraState;
  bodies: readonly BodyVisualDescriptor[];
  infrastructure: readonly InfrastructureAttachment[];
  contributions: readonly SpatialContribution[];
}>;
