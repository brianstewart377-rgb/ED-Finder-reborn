/**
 * Renderer-neutral Stage 27 spatial contracts.
 *
 * Coordinates in galaxy scenes are canonical Elite light-years. Renderers may
 * choose a precision strategy internally, but no renderer scale is domain
 * truth and no renderer-specific type may cross this boundary.
 */
export type RepresentationClass =
  'AUTHORITATIVE' | 'DERIVED' | 'PLANNED' | 'SCHEMATIC' | 'AMBIENT';

export type Vec3Ly = Readonly<{ x: number; y: number; z: number }>;

export type Bounds3Ly = Readonly<{
  min: Vec3Ly;
  max: Vec3Ly;
}>;

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

export type CameraState = Readonly<{
  focusLy: Vec3Ly;
  distanceLy: number;
  bearingRad: number;
  pitchRad: number;
  projection: 'perspective' | 'orthographic';
  revision: number;
}>;

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

/** Product-neutral point data consumed by the Galaxy renderer. */
export type GalaxySystemPoint = Readonly<{
  systemId64: string;
  name: string;
  positionLy: Vec3Ly;
  /** Known application targets retained before the viewport background sample. */
  reserved?: true;
  primaryStar?: Readonly<{
    type?: string;
    subtype?: string;
  }>;
  summary?: Readonly<{
    distanceLy?: number;
    population?: number;
    primaryEconomy?: string;
    secondaryEconomy?: string;
    security?: string;
    allegiance?: string;
    government?: string;
  }>;
}>;

export type GalaxySystemsPayload = Readonly<{
  systems: readonly GalaxySystemPoint[];
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

export type GalaxySceneContract = Readonly<{
  kind: 'galaxy';
  revision: number;
  camera: CameraState;
  selection: readonly SpatialTarget[];
  contributions: readonly SpatialContribution[];
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

export type SpatialSceneContract = GalaxySceneContract | SystemSceneContract;

export type RuntimeCommand =
  | Readonly<{ type: 'LOAD_SCENE'; scene: SpatialSceneContract }>
  | Readonly<{
      type: 'PATCH_CONTRIBUTION';
      contribution: SpatialContribution;
    }>
  | Readonly<{
      type: 'SET_CAMERA';
      camera: CameraState | SystemCameraState;
      transition?: Readonly<{ durationMs: number; reducedMotion: boolean }>;
    }>
  | Readonly<{
      type: 'FLY_TO';
      target: SpatialTarget;
      reducedMotion: boolean;
    }>
  | Readonly<{ type: 'HOVER'; screenX: number; screenY: number }>
  | Readonly<{ type: 'CLEAR_HOVER' }>
  | Readonly<{ type: 'PICK'; screenX: number; screenY: number }>
  | Readonly<{ type: 'RESIZE'; width: number; height: number; dpr: number }>
  | Readonly<{
      type: 'REBUILD_RESOURCES';
      reason: 'backend-change' | 'device-loss' | 'context-loss';
    }>;

export type RuntimeEvent =
  | Readonly<{ type: 'READY'; backend: 'WEBGPU' | 'WEBGL2' }>
  | Readonly<{
      type: 'SCENE_APPLIED';
      sceneRevision: number;
      renderedLayers: readonly RenderedLayerReceipt[];
    }>
  | Readonly<{
      type: 'CONTRIBUTION_APPLIED';
      contributionId: string;
      contributionRevision: number;
      renderedLayers: readonly RenderedLayerReceipt[];
    }>
  | Readonly<{
      type: 'CAMERA_CHANGED';
      camera: CameraState | SystemCameraState;
    }>
  | Readonly<{
      type: 'TARGET_HOVERED';
      target?: SpatialTarget;
      /** Pointer position in canvas-local CSS pixels, when pointer initiated. */
      screenX?: number;
      screenY?: number;
    }>
  | Readonly<{ type: 'TARGET_PICKED'; target?: SpatialTarget }>
  | Readonly<{ type: 'TRANSITION_FINISHED'; target?: SpatialTarget }>
  | Readonly<{
      type: 'RESOURCE_LOST' | 'RECOVERED';
      detail: string;
    }>
  | Readonly<{
      type: 'METRICS';
      frameMs: number;
      visible: number;
      drawCalls: number;
      resources: number;
      bufferBytes: number;
    }>;

export type RenderedLayerReceipt = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerId: string;
  layerVersion: number;
  representation: RepresentationClass;
  acceptedTargetCount: number;
  renderedTargetCount: number;
  /** Layer targets by presentation batch; shared identities can occur in multiple layers. */
  renderedBatchCounts?: Readonly<{ catalogue: number; finder: number }>;
  sourceGeneration?: string;
}>;

export type RuntimeCommandDispatchResult =
  | Readonly<{ status: 'executed' }>
  | Readonly<{ status: 'ignored'; reason: 'inactive' | 'stale' }>
  | Readonly<{
      status: 'unsupported';
      command: Exclude<RuntimeCommand['type'], 'RESIZE'>;
    }>;

export type RuntimeEventListener = (event: RuntimeEvent) => void;

export type SpatialViewport = Readonly<{
  width: number;
  height: number;
  dpr: number;
}>;

export type SpatialRendererBackend = 'WEBGPU' | 'WEBGL2';
export type SpatialRuntimeFailure =
  'BACKEND_UNAVAILABLE' | 'INITIALIZATION_FAILED' | 'RUNTIME_FAILED';

export type SpatialRuntimeStatus =
  | Readonly<{ state: 'created' | 'starting' | 'disposed' }>
  | Readonly<{ state: 'ready'; backend: SpatialRendererBackend }>
  | Readonly<{ state: 'failed'; failure: SpatialRuntimeFailure }>;

export interface SpatialRuntime {
  getStatus(): SpatialRuntimeStatus;
  start(): Promise<SpatialRuntimeStatus>;
  dispatch(command: RuntimeCommand): RuntimeCommandDispatchResult;
  subscribe(listener: RuntimeEventListener): () => void;
  /** @deprecated Dispatch a renderer-neutral RESIZE command instead. */
  resize(viewport: SpatialViewport): void;
  dispose(): void;
}

export type SpatialRuntimeStatusListener = (
  status: SpatialRuntimeStatus,
) => void;
