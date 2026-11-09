<script lang="ts">
  import { onMount, tick, untrack, type Snippet } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';
  import { resolve } from '$app/paths';
  import { FINDER_F4_ENABLED } from '$lib/config';

  import type {
    Bounds3Ly,
    BodyRef,
    BodyVisualDescriptor,
    CameraState,
    GalaxySystemPoint,
    Provenance,
    RuntimeEvent,
    SpatialRuntime,
    SpatialRuntimeStatus,
    SpatialContribution,
    SpatialSceneContract,
    SpatialTarget,
    SystemCameraState,
  } from './contracts';
  import {
    GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
    elevateGalaxyCameraByPixels,
    elevateGalaxyCameraStep,
    keepGalaxyInView,
    orbitGalaxyCamera,
    panGalaxyCamera,
    pinchGalaxyCamera,
    topDownGalaxyCamera,
    zoomGalaxyCamera,
    zoomGalaxyCameraTowards,
    type GalaxyCameraPoint,
    type GalaxyCameraViewport,
  } from './galaxy-camera';
  import {
    CATALOGUE_DENSITY_LAYER_ID,
    catalogueDensitySceneLayer,
  } from './galaxy-density';
  import { galaxyReferenceGrid } from './galaxy-grid';
  import { galaxyRegionsSceneLayer } from './galaxy-regions';
  import {
    galaxyLabelCandidates,
    galaxySystemPointIndex,
    layoutGalaxyLabels,
    projectGalaxyLabel,
    type GalaxyLabelCandidate,
    type ProjectedGalaxyLabel,
  } from './galaxy-labels';
  import { fitSystemCameraToViewport, systemBodyLayout } from './system-scene';
  import { layoutSystemBodyLabels } from './system-labels';
  import GalaxySystemHoverCard from './GalaxySystemHoverCard.svelte';
  import SystemBodyLegend from './SystemBodyLegend.svelte';

  let {
    scene,
    focusTarget = null,
    focusRevision = 0,
    cameraRequest = null,
    cameraRequestRevision = 0,
    resetCamera: initialGalaxyCamera = null,
    contributionPatch = null,
    contributionPatchRevision = 0,
    onRuntimeEvent,
    onTransitionChange,
    onViewportChange,
    anchorPoint = null,
    onSystemSelect,
    systemInspectHref,
    onSystemInspect,
    controlsOverlay = false,
    cameraActions,
  } = $props<{
    /**
     * Presentation only: Explore floats the camera controls, readout, nearby
     * list and renderer status over the map instead of stacking them below it,
     * so the canvas gets the whole window, like the in-game map.
     */
    controlsOverlay?: boolean;
    cameraActions?: Snippet<[Snippet]>;
    scene?: SpatialSceneContract;
    focusTarget?: SpatialTarget | null;
    focusRevision?: number;
    cameraRequest?: CameraState | null;
    cameraRequestRevision?: number;
    /** Route-owned Reset view target retained while this canvas is suspended. */
    resetCamera?: CameraState | null;
    contributionPatch?: SpatialContribution | null;
    contributionPatchRevision?: number;
    onRuntimeEvent?: (event: RuntimeEvent) => void;
    onViewportChange?: (viewport: GalaxyCameraViewport) => void;
    anchorPoint?: GalaxySystemPoint | null;
    onSystemSelect?: (system: GalaxySystemPoint) => void;
    systemInspectHref?: (system: GalaxySystemPoint) => string;
    onSystemInspect?: (event: MouseEvent, system: GalaxySystemPoint) => void;
    onTransitionChange?: (
      active: boolean,
      camera?: CameraState | SystemCameraState | null,
    ) => void;
  }>();

  let canvas: HTMLCanvasElement;
  let host: HTMLDivElement;
  let runtime: SpatialRuntime | null = null;
  let status = $state<SpatialRuntimeStatus>({ state: 'created' });
  let resizeRevision = $state(0);
  let lastLoadedRevision = $state(-1);
  let lastFocusRevision = -1;
  let lastCameraRequestRevision = -1;
  let lastContributionPatchRevision = -1;
  let currentCamera = $state<CameraState | null>(null);
  let currentSystemCamera = $state<SystemCameraState | null>(null);
  let homeCamera: CameraState | null = null;
  let homeSystemCamera: SystemCameraState | null = null;
  // The contract overview of a system scene that loaded into a canvas with no
  // size yet (it sits in a hidden tab panel). It is fitted, and the camera
  // re-applied, on the first real size.
  let pendingSystemRefit: SystemCameraState | null = null;
  const canGalaxyNavigate = $derived(scene?.kind === 'galaxy');
  const canSystemNavigate = $derived(scene?.kind === 'system');
  const canNavigate = $derived(canGalaxyNavigate || canSystemNavigate);
  const cameraDensityContribution = $derived.by(() => {
    if (scene?.kind !== 'galaxy') return undefined;
    const contributions: readonly SpatialContribution[] = scene.contributions;
    for (const part of contributions) {
      // F4 reserves an empty contribution until its first density patch.
      const contribution: SpatialContribution =
        contributionPatch?.id === part.id &&
        part.revision < contributionPatch.revision
          ? contributionPatch
          : part;
      if (
        contribution.layers.some(
          (layer) => layer.id === CATALOGUE_DENSITY_LAYER_ID,
        )
      )
        return contribution;
    }
    return undefined;
  });
  const cameraDensity = $derived.by(() => {
    const contribution = cameraDensityContribution;
    if (!contribution) return undefined;
    // Camera receipts replace the scene object. Validate density only when
    // the contribution changes, so a drag never rescans its catalogue cells.
    const galaxyScene = untrack(() => scene);
    if (galaxyScene?.kind !== 'galaxy') return undefined;
    return catalogueDensitySceneLayer({
      ...galaxyScene,
      contributions: [contribution],
    })?.payload;
  });
  let cameraBounds = $state<Bounds3Ly | undefined>();
  let cameraBoundsGeneration: string | undefined;
  $effect(() => {
    const density = cameraDensity;
    if (!density) return;
    if (density.generationId !== cameraBoundsGeneration) {
      cameraBoundsGeneration = density.generationId;
      cameraBounds = undefined;
    }
    // Truncated responses describe only the densest cells. Retain the last
    // complete catalogue extent while a finer level refreshes this generation.
    if (density.complete) cameraBounds = density.boundsLy;
  });
  type ActivePointer = {
    id: number;
    pointerType: string;
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
    mode: 'pan' | 'orbit' | 'elevate';
  };
  const activePointers = new SvelteMap<number, ActivePointer>();
  let removeWindowDragListeners: (() => void) | null = null;
  let lastPickedId64 = $state<string | undefined>();
  let lastPickedRegionId = $state<string | undefined>();
  let lastHoveredRegionId = $state<string | undefined>();
  let lastHoveredBodyRef = $state<BodyRef | undefined>();
  const hoveredBody = $derived(
    scene?.kind === 'system' &&
      scene.systemId64 === lastHoveredBodyRef?.systemId64
      ? scene.bodies.find(
          (body: BodyVisualDescriptor) =>
            body.ref.bodyId === lastHoveredBodyRef?.bodyId,
        )
      : undefined,
  );
  $effect.pre(() => {
    if (
      scene?.kind !== 'system' ||
      scene.systemId64 !== lastHoveredBodyRef?.systemId64
    )
      lastHoveredBodyRef = undefined;
  });
  let cameraTransitionActive = $state(false);
  let hoveredSystemId64 = $state<string | undefined>();
  let hoverPosition = $state({ x: 16, y: 16 });
  let hoverClearTimeout: number | null = null;
  let tooltipHost = $state<HTMLDivElement>();
  let nearbyOpen = $state(false);
  const componentId = $props.id();
  const tooltipId = `${componentId}-system-tooltip`;
  const systemKeyboardHelpId = `${componentId}-system-keyboard-help`;
  let hoverOpener: HTMLElement | null = null;
  let restoringHoverFocus = false;
  let focusCardRequested = $state(false);
  const pointIndex = $derived(
    scene?.kind === 'galaxy' ? galaxySystemPointIndex(scene) : null,
  );
  const hoveredSystem = $derived(
    hoveredSystemId64
      ? (pointIndex?.get(hoveredSystemId64) ??
          (anchorPoint?.systemId64 === hoveredSystemId64 ? anchorPoint : null))
      : null,
  );
  let labelCandidates = $state.raw<GalaxyLabelCandidate[]>([]);
  let visibleGalaxyLabels = $state.raw<ProjectedGalaxyLabel[]>([]);
  let labelCamera = $state.raw<CameraState | null>(null);
  // Region names faded in the last layout (not reactive state).
  let lastMutedLabels = new Set<string>();
  // True until the nearby-name ranking has caught up with the camera. Hover
  // cards wait for this; the name overlay itself no longer does.
  const labelsMoving = $derived(
    cameraTransitionActive || labelCamera !== currentCamera,
  );
  const nearbySystems = $derived(
    labelCandidates
      .filter((label) => label.kind === 'system' && label.point)
      // Priority chooses membership/placement. Stable DOM order keeps Tab and
      // existing keyboard focus predictable when hover changes that priority.
      .sort(
        (left, right) =>
          left.text.localeCompare(right.text) ||
          left.key.localeCompare(right.key),
      ),
  );
  const renderedGalaxyLabels = $derived(
    [...visibleGalaxyLabels].sort((left, right) =>
      left.key.localeCompare(right.key),
    ),
  );
  $effect(() => {
    if (!focusCardRequested || !hoveredSystem || labelsMoving) return;
    void tick().then(() => {
      tooltipHost?.querySelector<HTMLButtonElement>('button')?.focus();
      focusCardRequested = false;
    });
  });
  $effect(() => {
    // A pointer no longer identifies the same star once the camera moves.
    // Keyboard-triggered cards instead remain associated with their trigger.
    void currentCamera;
    untrack(() => {
      if (!hoverOpener) dismissHover();
    });
  });
  let lastSettledFocusId64 = $state<string | undefined>();
  let lastSettledFocusBodyId = $state<number | undefined>();
  let lastAppliedSceneRevision = $state<number | undefined>();
  let lastAppliedContributionRevision = $state<number | undefined>();
  let renderedLayerIds = $state<string[]>([]);
  let pendingHoverPoint: { x: number; y: number } | null = null;
  let transitionCamera: CameraState | SystemCameraState | null = null;
  let pendingPickPoint: { screenX: number; screenY: number } | null = null;
  type PendingCameraMove =
    | { type: 'SET_CAMERA'; camera: CameraState; animate: boolean }
    | { type: 'FLY_TO'; target: SpatialTarget; reducedMotion: boolean };
  let pendingCameraMove: PendingCameraMove | null = null;
  let pendingScene: SpatialSceneContract | null = null;
  let recoverCurrentCamera = false;
  let hoverFrame: number | null = null;

  function setCameraTransitionActive(
    active: boolean,
    camera?: CameraState | SystemCameraState | null,
  ): void {
    cameraTransitionActive = active;
    onTransitionChange?.(active, camera);
  }

  function dispatchPendingFocus(): void {
    if (
      status.state !== 'ready' ||
      !runtime ||
      !focusTarget ||
      focusRevision === lastFocusRevision ||
      scene?.revision !== lastLoadedRevision
    ) {
      return;
    }
    const reducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    const result = runtime.dispatch({
      type: 'FLY_TO',
      target: focusTarget,
      reducedMotion,
    });
    if (result.status === 'executed') {
      lastFocusRevision = focusRevision;
      setCameraTransitionActive(!reducedMotion);
    }
  }

  function targetCount(value: SpatialSceneContract | undefined): number {
    return layerTargetCount(value, 'finder-systems');
  }

  function layerTargetCount(
    value: SpatialSceneContract | undefined,
    layerId: string,
  ): number {
    return (
      value?.contributions.reduce(
        (total, contribution) =>
          total +
          contribution.layers.reduce(
            (layerTotal, layer) =>
              layerTotal + (layer.id === layerId ? layer.targetCount : 0),
            0,
          ),
        0,
      ) ?? 0
    );
  }

  const statusLabel = $derived.by(() => {
    switch (status.state) {
      case 'ready':
        return `Renderer ready (${status.backend})`;
      case 'failed':
        return `Renderer unavailable (${status.failure})`;
      case 'disposed':
        return 'Renderer disposed';
      default:
        return 'Renderer starting';
    }
  });

  const statusBackend = $derived(
    status.state === 'ready' ? status.backend : undefined,
  );
  const sceneTargetCount = $derived(targetCount(scene));
  const systemTargetCount = $derived(layerTargetCount(scene, 'finder-systems'));
  $effect(() => {
    // Which names exist is decided on the settled view: ranking the nearby
    // catalogue scans every streamed star, so never do it on CAMERA_CHANGED
    // animation frames or during a held drag.
    void resizeRevision;
    const nextScene = scene;
    const camera = currentCamera;
    const regionId = lastHoveredRegionId;
    const systemId = hoveredSystemId64;
    const reference = anchorPoint;
    if (nextScene?.kind !== 'galaxy' || !camera || !host) {
      labelCandidates = [];
      visibleGalaxyLabels = [];
      lastMutedLabels = new Set();
      return;
    }
    if (cameraTransitionActive) return;
    const timeout = window.setTimeout(() => {
      labelCandidates = galaxyLabelCandidates(
        { ...nextScene, camera },
        regionId,
        {
          hoveredSystemId64: systemId,
          anchor: reference ?? undefined,
          viewport: viewport(),
        },
      );
      labelCamera = camera;
    }, 180);
    return () => window.clearTimeout(timeout);
  });
  // Where each name sits is re-projected from the live camera on every change,
  // so a name stays on its star or in its region while you zoom and pan
  // instead of vanishing and reappearing somewhere else once the camera stops.
  // Projecting the bounded candidate set is cheap. Region names never jump to
  // make room: a name that would overlap a more important one fades instead,
  // and a region whose name point leaves the screen keeps its name inside the
  // visible part of the region.
  const regionLookup = $derived(
    scene?.kind === 'galaxy'
      ? galaxyRegionsSceneLayer(scene)?.payload.lookup
      : undefined,
  );
  $effect(() => {
    void resizeRevision;
    const camera = currentCamera;
    const candidates = labelCandidates;
    if (scene?.kind !== 'galaxy' || !camera || !host) return;
    if (cameraTransitionActive) return;
    const solved = layoutGalaxyLabels(candidates, camera, viewport(), {
      previouslyMuted: lastMutedLabels,
      ...(regionLookup ? { regionLookup } : {}),
    });
    lastMutedLabels = new Set(
      solved.filter((label) => label.muted).map((label) => label.key),
    );
    visibleGalaxyLabels = solved;
  });
  const atlasRegionLabels = $derived(
    visibleGalaxyLabels.filter(
      (label) => label.kind === 'region' && label.placement !== 'map',
    ),
  );
  const currentGrid = $derived.by(() => {
    // Keep resizeRevision a reactive dependency (see visibleGalaxyLabels).
    void resizeRevision;
    if (!canGalaxyNavigate || !currentCamera || !host) return null;
    return galaxyReferenceGrid(currentCamera, {
      width: Math.max(1, host.clientWidth),
      height: Math.max(1, host.clientHeight),
    });
  });

  function lightYearInterval(value: number | undefined): string {
    if (!value) return '—';
    return `${value.toLocaleString(undefined, {
      maximumFractionDigits: value < 1 ? 2 : 0,
    })} ly`;
  }

  function applyScene(nextScene: SpatialSceneContract): void {
    if (!runtime || status.state !== 'ready') return;
    // The contract's own overview camera, before any recovery substitution.
    const contractCamera =
      nextScene.kind === 'system' ? nextScene.camera : null;
    const restoring = FINDER_F4_ENABLED && recoverCurrentCamera;
    if (restoring) {
      // F4 camera receipts do not rebuild the parent's scene snapshot. A
      // context recovery must resume the view currently owned by the canvas.
      const camera = untrack(() =>
        nextScene.kind === 'galaxy' ? currentCamera : currentSystemCamera,
      );
      if (nextScene.kind === 'galaxy' && camera && 'focusLy' in camera) {
        nextScene = { ...nextScene, camera };
      } else if (
        nextScene.kind === 'system' &&
        camera &&
        'systemId64' in camera &&
        camera.systemId64 === nextScene.systemId64
      ) {
        nextScene = { ...nextScene, camera };
      }
    }
    // Density can be patched while a scene waits for a camera flight to settle,
    // or after the parent last built a scene that must now be recovered. Keep
    // that newer contribution in its reserved position without a second patch
    // dispatch or making patch receipts a scene-effect dependency.
    const latestPatch = FINDER_F4_ENABLED
      ? untrack(() => contributionPatch)
      : null;
    if (
      latestPatch &&
      nextScene.contributions.some(
        (part) =>
          part.id === latestPatch.id && part.revision < latestPatch.revision,
      )
    ) {
      nextScene = {
        ...nextScene,
        contributions: nextScene.contributions.map((part) =>
          part.id === latestPatch.id ? latestPatch : part,
        ),
      };
    }
    if (
      runtime.dispatch({ type: 'LOAD_SCENE', scene: nextScene }).status ===
      'executed'
    ) {
      if (!homeCamera && nextScene.kind === 'galaxy')
        homeCamera = untrack(() => initialGalaxyCamera) ?? nextScene.camera;
      if (nextScene.kind === 'system' && contractCamera) {
        // Fit the contract's overview to this canvas; Reset view returns to
        // it. A context recovery resumes the camera the canvas already owns
        // and must not fit (and compound) that already-fitted distance.
        //
        // A canvas inside a hidden tab panel has no size yet. Fitting to the
        // 1 × 1 stand-in would pick the wrong distance, so wait for a real
        // size instead (refitSystemOverview).
        const measurable = host.clientWidth >= 2 && host.clientHeight >= 2;
        const fitted = measurable
          ? fitSystemCameraToViewport(contractCamera, viewport())
          : contractCamera;
        pendingSystemRefit = measurable || restoring ? null : contractCamera;
        homeSystemCamera = fitted;
        currentSystemCamera = nextScene.camera;
        if (
          !restoring &&
          fitted.semanticDistance !== nextScene.camera.semanticDistance
        )
          setSystemCamera({ ...fitted, revision: fitted.revision + 1 });
      }
      lastLoadedRevision = nextScene.revision;
      recoverCurrentCamera = false;
    }
  }

  $effect(() => {
    if (
      status.state !== 'ready' ||
      !runtime ||
      !scene ||
      scene.revision === lastLoadedRevision
    ) {
      return;
    }
    if (cameraTransitionActive) {
      pendingScene = scene;
      return;
    }
    applyScene(scene);
  });

  $effect(() => {
    if (
      status.state !== 'ready' ||
      !runtime ||
      !contributionPatch ||
      contributionPatchRevision === lastContributionPatchRevision ||
      scene?.revision !== lastLoadedRevision
    ) {
      return;
    }
    const result = runtime.dispatch({
      type: 'PATCH_CONTRIBUTION',
      contribution: contributionPatch,
    });
    if (result.status !== 'ignored' || result.reason !== 'inactive') {
      lastContributionPatchRevision = contributionPatchRevision;
    }
  });

  function dispatchCameraMove(move: PendingCameraMove): void {
    if (!runtime || status.state !== 'ready') return;

    if (move.type === 'SET_CAMERA') {
      const { camera, animate } = move;
      const reducedMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      ).matches;
      const result = runtime.dispatch({
        type: 'SET_CAMERA',
        camera,
        ...(animate ? { transition: { durationMs: 650, reducedMotion } } : {}),
      });
      if (result.status === 'executed') {
        currentCamera = camera;
        if (animate && !reducedMotion) {
          transitionCamera = camera;
          setCameraTransitionActive(true, camera);
        }
      }
      return;
    }

    const { target, reducedMotion } = move;
    const result = runtime.dispatch({
      type: 'FLY_TO',
      target,
      reducedMotion,
    });
    if (result.status === 'executed') {
      setCameraTransitionActive(!reducedMotion);
    }
  }

  function setCamera(camera: CameraState, animate = false): void {
    if (!runtime || status.state !== 'ready') return;
    if (cameraTransitionActive) {
      pendingCameraMove = { type: 'SET_CAMERA', camera, animate };
      return;
    }
    dispatchCameraMove({ type: 'SET_CAMERA', camera, animate });
  }

  function setSystemCamera(camera: SystemCameraState): void {
    if (!runtime || status.state !== 'ready') return;
    if (runtime.dispatch({ type: 'SET_CAMERA', camera }).status === 'executed')
      currentSystemCamera = camera;
  }

  $effect(() => {
    if (
      status.state !== 'ready' ||
      !runtime ||
      !cameraRequest ||
      cameraRequestRevision === lastCameraRequestRevision ||
      scene?.revision !== lastLoadedRevision
    )
      return;
    setCamera(cameraRequest, true);
    lastCameraRequestRevision = cameraRequestRevision;
  });

  $effect(() => {
    if (scene?.kind === 'galaxy' && currentCamera === null)
      currentCamera = scene.camera;
    if (scene?.kind === 'system' && currentSystemCamera === null)
      currentSystemCamera = scene.camera;
  });

  function viewport(): { width: number; height: number } {
    return {
      width: Math.max(1, host.clientWidth),
      height: Math.max(1, host.clientHeight),
    };
  }

  /**
   * Fit a system overview that loaded before the canvas had a size, then apply
   * it as a camera command (the same step Reset view takes) so the first
   * frame at the real size is drawn. A camera the commander or the page has
   * already moved is left where it is.
   */
  function refitSystemOverview(): void {
    const contract = pendingSystemRefit;
    if (
      !contract ||
      !runtime ||
      status.state !== 'ready' ||
      host.clientWidth < 2 ||
      host.clientHeight < 2
    )
      return;
    pendingSystemRefit = null;
    // The scene may have changed to a galaxy while the canvas was hidden.
    if (untrack(() => scene?.kind) !== 'system') return;
    const fitted = fitSystemCameraToViewport(contract, viewport());
    homeSystemCamera = fitted;
    const current = untrack(() => currentSystemCamera);
    const untouched =
      !current ||
      (current.semanticDistance === contract.semanticDistance &&
        current.bearingRad === contract.bearingRad &&
        current.pitchRad === contract.pitchRad);
    if (untouched)
      setSystemCamera({
        ...fitted,
        revision: Math.max(current?.revision ?? 0, contract.revision) + 1,
      });
  }

  /**
   * Apply a pan, zoom or pinch the commander made, keeping the galaxy on
   * screen: zoomed out it stays centred, zoomed in it can be explored but
   * never dragged away.
   */
  function navigateGalaxy(from: CameraState, next: CameraState): void {
    setCamera(keepGalaxyInView(next, viewport(), from));
  }

  function pan(x: number, y: number): void {
    if (currentCamera)
      navigateGalaxy(
        currentCamera,
        panGalaxyCamera(
          currentCamera,
          x,
          y,
          viewport(),
          cameraBounds ?? GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
        ),
      );
  }

  function zoom(delta: number): void {
    if (currentCamera)
      navigateGalaxy(currentCamera, zoomGalaxyCamera(currentCamera, delta));
  }

  function elevate(direction: 1 | -1): void {
    if (currentCamera)
      setCamera(
        elevateGalaxyCameraStep(currentCamera, direction, cameraBounds),
      );
  }

  function orbit(bearing: number, pitch = 0): void {
    if (currentCamera)
      setCamera(orbitGalaxyCamera(currentCamera, bearing, pitch));
  }

  function orbitSystem(bearing: number, pitch = 0): void {
    if (!currentSystemCamera) return;
    setSystemCamera({
      ...currentSystemCamera,
      bearingRad: currentSystemCamera.bearingRad + bearing,
      pitchRad: Math.max(
        0.08,
        Math.min(1.42, currentSystemCamera.pitchRad + pitch),
      ),
      revision: currentSystemCamera.revision + 1,
    });
  }

  // A system overview may fit beyond the default ceiling (large hierarchies,
  // narrow canvases); zooming out must always reach that overview distance.
  function systemZoomCeiling(): number {
    const overview =
      homeSystemCamera ?? (scene?.kind === 'system' ? scene.camera : null);
    return Math.max(120, (overview?.semanticDistance ?? 0) * 1.5);
  }
  function zoomSystem(delta: number): void {
    if (!currentSystemCamera) return;
    setSystemCamera({
      ...currentSystemCamera,
      semanticDistance: Math.max(
        6,
        Math.min(
          systemZoomCeiling(),
          currentSystemCamera.semanticDistance * Math.exp(delta * 0.0014),
        ),
      ),
      revision: currentSystemCamera.revision + 1,
    });
  }

  function resetCamera(): void {
    if (canSystemNavigate && homeSystemCamera) {
      setSystemCamera({
        ...homeSystemCamera,
        revision: (currentSystemCamera?.revision ?? 0) + 1,
      });
    } else if (homeCamera)
      setCamera(
        { ...homeCamera, revision: (currentCamera?.revision ?? 0) + 1 },
        true,
      );
  }

  function cameraKey(event: KeyboardEvent): void {
    if (
      !canNavigate ||
      event.target !== host ||
      event.altKey ||
      event.metaKey ||
      event.ctrlKey
    )
      return;
    const step = event.shiftKey ? 80 : 32;
    if (canSystemNavigate) {
      switch (event.key) {
        case 'ArrowLeft':
          orbitSystem(-0.12);
          break;
        case 'ArrowRight':
          orbitSystem(0.12);
          break;
        case 'ArrowUp':
          orbitSystem(0, 0.09);
          break;
        case 'ArrowDown':
          orbitSystem(0, -0.09);
          break;
        case '+':
        case '=':
          zoomSystem(-180);
          break;
        case '-':
        case '_':
          zoomSystem(180);
          break;
        case 'Home':
          resetCamera();
          break;
        default:
          return;
      }
      event.preventDefault();
      return;
    }
    switch (event.key) {
      case 'ArrowLeft':
        pan(-step, 0);
        break;
      case 'ArrowRight':
        pan(step, 0);
        break;
      case 'ArrowUp':
        pan(0, -step);
        break;
      case 'ArrowDown':
        pan(0, step);
        break;
      case '+':
      case '=':
        zoom(-200);
        break;
      case '-':
      case '_':
        zoom(200);
        break;
      case 'q':
      case 'Q':
        orbit(-0.12);
        break;
      case 'e':
      case 'E':
        orbit(0.12);
        break;
      case 'Home':
        resetCamera();
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  /**
   * Scrolling zooms the map, over the canvas or over the control bar that
   * floats on it (the bar sits on the bottom of the map, where a commander
   * expects the wheel to keep working).
   */
  function mapWheel(event: WheelEvent): void {
    if (!canNavigate) return;
    event.preventDefault();
    const pixels =
      event.deltaY *
      (event.deltaMode === 1
        ? 16
        : event.deltaMode === 2
          ? host.clientHeight
          : 1);
    const boundedPixels = Math.max(-400, Math.min(400, pixels));
    if (canSystemNavigate) zoomSystem(boundedPixels);
    else if (currentCamera)
      navigateGalaxy(
        currentCamera,
        zoomGalaxyCameraTowards(
          currentCamera,
          boundedPixels,
          canvasPoint({ x: event.clientX, y: event.clientY }),
          viewport(),
          cameraBounds,
        ),
      );
  }

  function forwardMapWheel(node: HTMLElement): () => void {
    // A pop-out opened from the bar (Sources) renders inside it; its own
    // content must keep scrolling instead of zooming the map.
    const overBar = (event: WheelEvent): void => {
      if ((event.target as Element | null)?.closest('[role="dialog"]')) return;
      mapWheel(event);
    };
    node.addEventListener('wheel', overBar, { passive: false });
    return () => node.removeEventListener('wheel', overBar);
  }

  function pointerDown(event: PointerEvent): void {
    if ((event.target as Element).closest('[data-map-interactive]')) return;
    if (!canNavigate) {
      pick(event);
      return;
    }
    if (event.button !== 0 && event.button !== 2) return;
    if (status.state !== 'ready' || activePointers.has(event.pointerId)) return;
    // A second touch joins a Galaxy gesture. Other additional pointers never
    // replace an active drag or change the System map's existing controls.
    if (
      activePointers.size > 0 &&
      (!canGalaxyNavigate ||
        event.pointerType !== 'touch' ||
        activePointers.size >= 2 ||
        [...activePointers.values()].some(
          (pointer) => pointer.pointerType !== 'touch',
        ))
    )
      return;
    // Keep keyboard focus on the semantic host; the browser's default pointer
    // focus would otherwise land on the hidden canvas and scroll the page.
    event.preventDefault();
    host.focus({ preventScroll: true });
    clearHover();
    activePointers.set(event.pointerId, {
      id: event.pointerId,
      pointerType: event.pointerType,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
      mode:
        canSystemNavigate || event.shiftKey || event.button === 2
          ? 'orbit'
          : event.altKey
            ? 'elevate'
            : 'pan',
    });
    if (activePointers.size === 2) {
      // Neither finger may become a click after participating in a gesture.
      for (const pointer of activePointers.values()) pointer.moved = true;
    }
    try {
      host.setPointerCapture?.(event.pointerId);
    } catch (error) {
      // Synthetic PointerEvents have no browser-owned active pointer to
      // capture. The window listeners still complete and cancel that gesture.
      if (!(error instanceof DOMException) || error.name !== 'NotFoundError')
        throw error;
    }
    if (!removeWindowDragListeners) installWindowDragListeners();
  }

  function installWindowDragListeners(): void {
    removeWindowDragListeners?.();
    const move = (event: PointerEvent): void => windowPointerMove(event);
    const up = (event: PointerEvent): void => windowPointerUp(event);
    const cancel = (event: PointerEvent): void => windowPointerCancel(event);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    removeWindowDragListeners = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      removeWindowDragListeners = null;
    };
  }

  function pointerMove(event: PointerEvent): void {
    if (
      activePointers.size > 0 ||
      (event.target as Element).closest('[data-map-interactive]')
    )
      return;
    hover(event);
  }

  function canvasPoint(point: GalaxyCameraPoint): GalaxyCameraPoint {
    const bounds = (
      host.querySelector('canvas') ?? host
    ).getBoundingClientRect();
    return { x: point.x - bounds.left, y: point.y - bounds.top };
  }

  function windowPointerMove(event: PointerEvent): void {
    const drag = activePointers.get(event.pointerId);
    if (!drag) return;
    if (activePointers.size === 2 && canGalaxyNavigate && currentCamera) {
      const [first, second] = [...activePointers.values()] as [
        ActivePointer,
        ActivePointer,
      ];
      const previous = [canvasPoint(first), canvasPoint(second)] as const;
      drag.x = event.clientX;
      drag.y = event.clientY;
      navigateGalaxy(
        currentCamera,
        pinchGalaxyCamera(
          currentCamera,
          previous,
          [canvasPoint(first), canvasPoint(second)],
          viewport(),
          cameraBounds,
        ),
      );
      return;
    }
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    if (
      Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) <
        4 &&
      !drag.moved
    )
      return;
    drag.moved = true;
    drag.x = event.clientX;
    drag.y = event.clientY;
    if (canSystemNavigate) orbitSystem(-deltaX * 0.006, deltaY * 0.004);
    else if (drag.mode === 'orbit') orbit(-deltaX * 0.006, deltaY * 0.004);
    else if (drag.mode === 'elevate' && currentCamera)
      setCamera(
        elevateGalaxyCameraByPixels(
          currentCamera,
          -deltaY,
          viewport(),
          cameraBounds,
        ),
      );
    else pan(-deltaX, -deltaY);
  }

  function releasePointerCapture(id: number): void {
    if (host.hasPointerCapture?.(id)) host.releasePointerCapture(id);
  }

  function windowPointerUp(event: PointerEvent): void {
    const completed = activePointers.get(event.pointerId);
    if (!completed) return;
    activePointers.delete(event.pointerId);
    releasePointerCapture(event.pointerId);
    if (activePointers.size === 0) removeWindowDragListeners?.();
    if (!completed.moved && (canSystemNavigate || completed.mode === 'pan'))
      pick(event);
  }

  function windowPointerCancel(event?: PointerEvent): void {
    if (event && !activePointers.has(event.pointerId)) return;
    const ids = [...activePointers.keys()];
    activePointers.clear();
    removeWindowDragListeners?.();
    for (const id of ids) releasePointerCapture(id);
    clearHover();
  }

  $effect(() => {
    if (
      status.state !== 'ready' ||
      !runtime ||
      !focusTarget ||
      focusRevision === lastFocusRevision ||
      scene?.revision !== lastLoadedRevision
    ) {
      return;
    }
    const reducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    if (cameraTransitionActive) {
      pendingCameraMove = {
        type: 'FLY_TO',
        target: focusTarget,
        reducedMotion,
      };
      lastFocusRevision = focusRevision;
      return;
    }
    dispatchPendingFocus();
  });

  function pick(event: PointerEvent): void {
    if (event.button !== 0 || status.state !== 'ready' || !runtime) return;
    const activeCanvas = host.querySelector('canvas');
    if (!activeCanvas) return;
    const bounds = activeCanvas.getBoundingClientRect();
    const point = {
      screenX: event.clientX - bounds.left,
      screenY: event.clientY - bounds.top,
    };
    if (cameraTransitionActive) {
      pendingPickPoint = point;
      return;
    }
    runtime.dispatch({ type: 'PICK', ...point });
  }

  function hover(event: PointerEvent): void {
    if (event.pointerType === 'touch' || status.state !== 'ready' || !runtime) {
      return;
    }
    const activeCanvas = host.querySelector('canvas');
    if (!activeCanvas) return;
    const bounds = activeCanvas.getBoundingClientRect();
    pendingHoverPoint = {
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    };
    if (hoverFrame !== null) return;
    hoverFrame = window.requestAnimationFrame(() => {
      hoverFrame = null;
      const point = pendingHoverPoint;
      pendingHoverPoint = null;
      if (!point || status.state !== 'ready' || !runtime) return;
      runtime.dispatch({ type: 'HOVER', screenX: point.x, screenY: point.y });
    });
  }

  function clearHover(): void {
    lastHoveredBodyRef = undefined;
    pendingHoverPoint = null;
    if (hoverFrame !== null) {
      window.cancelAnimationFrame(hoverFrame);
      hoverFrame = null;
    }
    if (status.state === 'ready' && runtime) {
      runtime.dispatch({ type: 'CLEAR_HOVER' });
    }
    scheduleHoverClear();
  }

  function keepHover(): void {
    if (hoverClearTimeout !== null) window.clearTimeout(hoverClearTimeout);
    hoverClearTimeout = null;
  }

  function dismissHover(): void {
    keepHover();
    if (!hoveredSystemId64) return;
    focusCardRequested = false;
    if (tooltipHost?.contains(document.activeElement)) {
      restoringHoverFocus = true;
      (hoverOpener?.isConnected ? hoverOpener : host).focus({
        preventScroll: true,
      });
      restoringHoverFocus = false;
    }
    hoveredSystemId64 = undefined;
    onRuntimeEvent?.({
      type: 'TARGET_HOVERED',
      target: lastHoveredRegionId
        ? { kind: 'region', id: lastHoveredRegionId }
        : undefined,
    });
  }

  function scheduleHoverClear(): void {
    keepHover();
    // Allow crossing the short gap between a star/label and its card. Focus
    // within either the trigger or card keeps it open; Tab is never captured.
    hoverClearTimeout = window.setTimeout(() => {
      hoverClearTimeout = null;
      if (tooltipHost?.contains(document.activeElement)) return;
      const focused = document.activeElement as HTMLElement | null;
      if (
        hoveredSystemId64 &&
        focused?.dataset.systemId64 === hoveredSystemId64
      )
        return;
      dismissHover();
    }, 220);
  }

  function showSystem(
    system: GalaxySystemPoint,
    event?: PointerEvent | FocusEvent,
  ): void {
    if (restoringHoverFocus) return;
    keepHover();
    lastHoveredRegionId = undefined;
    hoveredSystemId64 = system.systemId64;
    hoverOpener =
      event && !('clientX' in event)
        ? (event.currentTarget as HTMLElement)
        : null;
    if (event && 'clientX' in event) {
      const bounds = host.getBoundingClientRect();
      hoverPosition = {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      };
    } else {
      const projected = currentCamera
        ? projectGalaxyLabel(system.positionLy, currentCamera, viewport())
        : null;
      hoverPosition = { x: projected?.xPx ?? 16, y: projected?.yPx ?? 16 };
    }
    onRuntimeEvent?.({
      type: 'TARGET_HOVERED',
      target: {
        kind: 'system',
        systemId64: system.systemId64,
        name: system.name,
      },
    });
  }

  function systemKey(event: KeyboardEvent, system: GalaxySystemPoint): void {
    if (event.key !== 'ArrowRight') return;
    event.preventDefault();
    event.stopPropagation();
    showSystem(system);
    hoverOpener = event.currentTarget as HTMLElement;
    focusCardRequested = true;
  }

  function selectSystem(system: GalaxySystemPoint): void {
    if (
      tooltipHost?.contains(document.activeElement) ||
      document.activeElement?.classList.contains('galaxy-label--system')
    ) {
      // The flight hides map overlays. Keep keyboard focus on the persistent
      // canvas host before removing a focused card or hiding a label.
      host.focus({ preventScroll: true });
      hoverOpener = null;
      dismissHover();
    }
    if (onSystemSelect) onSystemSelect(system);
    else {
      const target = { kind: 'system', systemId64: system.systemId64 } as const;
      onRuntimeEvent?.({ type: 'TARGET_PICKED', target });
      dispatchCameraMove({
        type: 'FLY_TO',
        target,
        reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)')
          .matches,
      });
    }
  }

  // The System Map names its bodies without waiting for a hover. This is a
  // projection of the camera the renderer already holds; the overlay below is
  // inert and aria-hidden, and each name stays in the hover card and the
  // Inspect Bodies list.
  const systemLayouts = $derived(
    scene?.kind === 'system' ? systemBodyLayout(scene.bodies) : [],
  );
  const systemLabels = $derived.by(() => {
    void resizeRevision;
    const camera = currentSystemCamera;
    if (
      scene?.kind !== 'system' ||
      !host ||
      !camera ||
      camera.systemId64 !== scene.systemId64
    ) {
      return [];
    }
    return layoutSystemBodyLabels(systemLayouts, camera, {
      width: host.clientWidth,
      height: host.clientHeight,
    });
  });

  const cardPosition = $derived.by(() => {
    void resizeRevision;
    const width = host?.clientWidth ?? 640;
    const height = host?.clientHeight ?? 360;
    return {
      x: Math.max(8, Math.min(hoverPosition.x + 14, width - 292)),
      y: Math.max(8, Math.min(hoverPosition.y + 14, height - 240)),
    };
  });

  onMount(() => {
    let mounted = true;
    let activeRuntime: SpatialRuntime | null = null;
    let observer: ResizeObserver | null = null;
    let unsubscribe = (): void => undefined;
    let wheel: ((event: WheelEvent) => void) | null = null;

    const loadBabylonAdapter = async (): Promise<
      typeof import('./babylon/adapter')
    > => {
      let lastError: unknown;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          return await import('./babylon/adapter');
        } catch (error) {
          lastError = error;
          if (attempt === 0) {
            await new Promise((resolve) => window.setTimeout(resolve, 80));
          }
        }
      }
      throw lastError;
    };

    const startRuntime = async (): Promise<void> => {
      let adapter: typeof import('./babylon/adapter');
      try {
        adapter = await loadBabylonAdapter();
      } catch (error) {
        if (mounted) {
          status = { state: 'failed', failure: 'INITIALIZATION_FAILED' };
          console.error('Failed to load the Babylon spatial adapter', error);
        }
        return;
      }
      if (!mounted) return;

      const nextRuntime = adapter.createBabylonSpatialRuntime(
        canvas,
        (nextStatus) => {
          if (mounted) status = nextStatus;
        },
      );
      activeRuntime = nextRuntime;
      runtime = nextRuntime;
      unsubscribe = nextRuntime.subscribe((event) => {
        if (!mounted) return;
        if (event.type === 'TARGET_PICKED') {
          lastPickedId64 =
            event.target?.kind === 'system'
              ? event.target.systemId64
              : undefined;
          lastPickedRegionId =
            event.target?.kind === 'region' ? event.target.id : undefined;
        } else if (event.type === 'TARGET_HOVERED') {
          lastHoveredRegionId =
            event.target?.kind === 'region' ? event.target.id : undefined;
          lastHoveredBodyRef =
            event.target?.kind === 'body' ? event.target.ref : undefined;
          if (event.target?.kind === 'system') {
            keepHover();
            hoverOpener = null;
            hoveredSystemId64 = event.target.systemId64;
            hoverPosition = { x: event.screenX ?? 16, y: event.screenY ?? 16 };
          } else scheduleHoverClear();
        } else if (event.type === 'SCENE_APPLIED') {
          lastAppliedSceneRevision = event.sceneRevision;
          renderedLayerIds = event.renderedLayers.map((layer) => layer.layerId);
        } else if (event.type === 'CONTRIBUTION_APPLIED') {
          lastAppliedContributionRevision = event.contributionRevision;
          renderedLayerIds = event.renderedLayers.map((layer) => layer.layerId);
        } else if (
          event.type === 'CAMERA_CHANGED' &&
          'focusLy' in event.camera
        ) {
          if (cameraTransitionActive) transitionCamera = event.camera;
          else currentCamera = event.camera;
        } else if (
          event.type === 'CAMERA_CHANGED' &&
          'systemId64' in event.camera
        ) {
          if (cameraTransitionActive) transitionCamera = event.camera;
          else currentSystemCamera = event.camera;
        } else if (event.type === 'TRANSITION_FINISHED') {
          if (event.target?.kind === 'system') {
            lastSettledFocusId64 = event.target.systemId64;
          } else if (event.target?.kind === 'body') {
            lastSettledFocusBodyId = event.target.ref.bodyId;
          }
          if (transitionCamera && 'focusLy' in transitionCamera) {
            currentCamera = transitionCamera;
          } else if (transitionCamera && 'systemId64' in transitionCamera) {
            currentSystemCamera = transitionCamera;
          }
          const settledCamera =
            transitionCamera ??
            (event.target?.kind === 'body'
              ? currentSystemCamera
              : currentCamera);
          setCameraTransitionActive(false, settledCamera);
          transitionCamera = null;
          const queuedMove = pendingCameraMove;
          pendingCameraMove = null;
          const nextScene = pendingScene;
          pendingScene = null;
          if (nextScene) {
            // Finder/refetch updates queued during a flight still carry the
            // parent's pre-flight camera. Its settle callback cannot update
            // that snapshot synchronously: loading it as-is would undo the
            // completed move. Keep the new contributions at the settled view;
            // an intentional change of scene kind or system owns its camera.
            if (
              FINDER_F4_ENABLED &&
              nextScene.kind === 'galaxy' &&
              settledCamera &&
              'focusLy' in settledCamera
            ) {
              applyScene({ ...nextScene, camera: settledCamera });
            } else if (
              FINDER_F4_ENABLED &&
              nextScene.kind === 'system' &&
              settledCamera &&
              'systemId64' in settledCamera &&
              nextScene.systemId64 === settledCamera.systemId64
            ) {
              applyScene({ ...nextScene, camera: settledCamera });
            } else {
              applyScene(nextScene);
            }
          }
          if (queuedMove) {
            dispatchCameraMove(queuedMove);
          }
          if (!cameraTransitionActive) {
            dispatchPendingFocus();
          }
          if (!cameraTransitionActive && pendingPickPoint) {
            const point = pendingPickPoint;
            pendingPickPoint = null;
            runtime?.dispatch({ type: 'PICK', ...point });
          }
        } else if (event.type === 'RECOVERED' && scene) {
          recoverCurrentCamera = FINDER_F4_ENABLED;
          lastLoadedRevision = -1;
        }
        onRuntimeEvent?.(event);
      });

      const resize = (width: number, height: number): void => {
        if (!activeRuntime) return;
        const viewport = {
          width: Math.max(1, Math.round(width)),
          height: Math.max(1, Math.round(height)),
          dpr: Math.min(2, Math.max(1, window.devicePixelRatio || 1)),
        };
        activeRuntime.dispatch({ type: 'RESIZE', ...viewport });
        refitSystemOverview();
        onViewportChange?.({ width: viewport.width, height: viewport.height });
        resizeRevision += 1;
        host
          .querySelector('canvas')
          ?.setAttribute('data-resize-revision', String(resizeRevision));
      };

      observer = new ResizeObserver(([entry]) => {
        if (!entry) return;
        resize(entry.contentRect.width, entry.contentRect.height);
      });
      observer.observe(host);
      wheel = mapWheel;
      host.addEventListener('wheel', wheel, { passive: false });
      resize(host.clientWidth, host.clientHeight);
      void nextRuntime.start();
    };

    void startRuntime();

    return () => {
      mounted = false;
      windowPointerCancel();
      // Pointer cancellation also schedules hover dismissal. Cancel that last
      // timer so it cannot inspect document or publish events after unmount.
      keepHover();
      if (wheel) host.removeEventListener('wheel', wheel);
      observer?.disconnect();
      unsubscribe();
      activeRuntime?.dispose();
      runtime = null;
      lastLoadedRevision = -1;
      lastFocusRevision = -1;
      lastCameraRequestRevision = -1;
      lastContributionPatchRevision = -1;
      lastPickedRegionId = undefined;
      lastHoveredRegionId = undefined;
      lastHoveredBodyRef = undefined;
      lastSettledFocusId64 = undefined;
      lastSettledFocusBodyId = undefined;
      lastAppliedSceneRevision = undefined;
      lastAppliedContributionRevision = undefined;
      cameraTransitionActive = false;
      transitionCamera = null;
      pendingCameraMove = null;
      pendingScene = null;
      pendingPickPoint = null;
      renderedLayerIds = [];
    };
  });
</script>

{#snippet galaxyCameraHelp()}
  <p class="camera-help">
    Drag to pan · Shift-drag to orbit · Alt/Option-drag to move up/down · Scroll
    or pinch to zoom · Two-finger drag to pan · Arrow keys to pan · Q/E to
    rotate
  </p>
{/snippet}

{#snippet galaxyCameraControls()}
  <div
    class="camera-toolbar"
    class:camera-toolbar--overlay={controlsOverlay}
    role="group"
    aria-label="Galaxy camera controls"
  >
    <button type="button" aria-label="Zoom in" onclick={() => zoom(-240)}
      >+</button
    >
    <button type="button" aria-label="Zoom out" onclick={() => zoom(240)}
      >−</button
    >
    <div class="camera-angle" role="group" aria-label="Camera angle">
      <button
        type="button"
        aria-pressed={currentCamera?.pitchRad === Math.PI / 2}
        onclick={() =>
          currentCamera && setCamera(topDownGalaxyCamera(currentCamera), true)}
        >Top down</button
      >
      <button
        type="button"
        aria-pressed={currentCamera?.pitchRad === 0.55}
        onclick={() =>
          currentCamera &&
          setCamera(
            {
              ...currentCamera,
              pitchRad: 0.55,
              revision: currentCamera.revision + 1,
            },
            true,
          )}>Tilted</button
      >
    </div>
    <button type="button" onclick={resetCamera}>Reset view</button>
    <button type="button" aria-label="Rotate left" onclick={() => orbit(-0.25)}
      >↶</button
    >
    <button type="button" aria-label="Rotate right" onclick={() => orbit(0.25)}
      >↷</button
    >
    <button type="button" aria-label="Move up" onclick={() => elevate(1)}
      >Up</button
    >
    <button type="button" aria-label="Move down" onclick={() => elevate(-1)}
      >Down</button
    >
    {@render cameraActions?.(galaxyCameraHelp)}
    <span
      class="camera-distance"
      data-camera-distance={currentCamera?.distanceLy}
      data-grid-step-ly={currentGrid?.minorStepLy}
    >
      <span
        >Range {Math.round(currentCamera?.distanceLy ?? 0).toLocaleString()} ly</span
      >
      <span>Grid {lightYearInterval(currentGrid?.minorStepLy)}</span>
      <span role="group" aria-label="Focus coordinates"
        >X {Math.round(currentCamera?.focusLy.x ?? 0).toLocaleString()} / Y {Math.round(
          currentCamera?.focusLy.y ?? 0,
        ).toLocaleString()} / Z {Math.round(
          currentCamera?.focusLy.z ?? 0,
        ).toLocaleString()} LY</span
      >
    </span>
  </div>
{/snippet}

{#snippet rendererStatus()}
  <p
    class="renderer-status"
    class:renderer-status--overlay={controlsOverlay}
    role="status"
    aria-live="polite"
    data-renderer-state={status.state}
    data-renderer-backend={statusBackend}
  >
    {statusLabel}
  </p>
{/snippet}

{#if canGalaxyNavigate}
  {#if !controlsOverlay}
    {@render galaxyCameraControls()}
    {@render galaxyCameraHelp()}
  {/if}
{:else if canSystemNavigate}
  <div class="camera-toolbar" role="group" aria-label="System camera controls">
    <button type="button" aria-label="Zoom in" onclick={() => zoomSystem(-220)}
      >+</button
    >
    <button type="button" aria-label="Zoom out" onclick={() => zoomSystem(220)}
      >−</button
    >
    <button
      type="button"
      aria-label="Rotate left"
      onclick={() => orbitSystem(-0.25)}>↶</button
    >
    <button
      type="button"
      aria-label="Rotate right"
      onclick={() => orbitSystem(0.25)}>↷</button
    >
    <button type="button" onclick={resetCamera}>Reset view</button>
    <span
      class="camera-distance"
      data-system-camera-distance={currentSystemCamera?.semanticDistance}
      >Semantic range {Math.round(
        currentSystemCamera?.semanticDistance ?? 0,
      )}</span
    >
  </div>
  <p class="camera-help">
    Drag to orbit · Scroll to zoom · Arrow keys rotate · Click a body for facts
  </p>
{/if}

<div class="map-systems-surface">
  {#if canGalaxyNavigate}
    <p id={systemKeyboardHelpId} class="system-keyboard-help">
      Press Right arrow for system card actions. Enter selects and focuses the
      system. Escape closes the card.
    </p>
  {/if}
  <!-- The Galaxy canvas implements keyboard navigation and has parallel native controls. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div
    class="spatial-canvas"
    bind:this={host}
    role={canNavigate ? 'application' : 'presentation'}
    aria-label={canGalaxyNavigate
      ? 'Interactive Galaxy map. Arrow keys pan, plus and minus zoom, Q and E rotate, Home resets.'
      : canSystemNavigate
        ? 'Interactive 3D system map. Arrow keys rotate, plus and minus zoom, Home resets, and bodies can be selected.'
        : undefined}
    tabindex={canNavigate ? 0 : -1}
    onkeydown={cameraKey}
    onpointerdown={pointerDown}
    onpointermove={pointerMove}
    onlostpointercapture={windowPointerCancel}
    oncontextmenu={(event) => {
      if (canNavigate) event.preventDefault();
    }}
    onpointerleave={clearHover}
    data-scene-target-count={sceneTargetCount}
    data-system-target-count={systemTargetCount}
    data-camera-transition-active={cameraTransitionActive}
    data-last-picked-id64={lastPickedId64}
    data-last-picked-region-id={lastPickedRegionId}
    data-last-hovered-region-id={lastHoveredRegionId}
    data-last-hovered-system-id={hoveredSystemId64}
    data-last-settled-focus-id64={lastSettledFocusId64}
    data-last-settled-focus-body-id={lastSettledFocusBodyId}
    data-applied-scene-revision={lastAppliedSceneRevision}
    data-applied-contribution-revision={lastAppliedContributionRevision}
    data-camera-revision={canGalaxyNavigate
      ? currentCamera?.revision
      : currentSystemCamera?.revision}
    data-camera-bearing={canGalaxyNavigate
      ? currentCamera?.bearingRad
      : currentSystemCamera?.bearingRad}
    data-camera-pitch={canGalaxyNavigate
      ? currentCamera?.pitchRad
      : currentSystemCamera?.pitchRad}
    data-rendered-layer-ids={renderedLayerIds.join(',')}
  >
    <canvas
      bind:this={canvas}
      data-spatial-canvas
      data-resize-revision={resizeRevision}
      tabindex="-1"
      aria-hidden="true"
    ></canvas>
    {#if hoveredBody}
      <div
        class="system-body-hover"
        data-hovered-body-id={hoveredBody.ref.bodyId}
      >
        <strong>{hoveredBody.name ?? `Body ${hoveredBody.ref.bodyId}`}</strong>
        <span
          >{hoveredBody.bodyClass?.value ??
            hoveredBody.class?.value ??
            'Unknown body type'}</span
        >
        {#if hoveredBody.bodyClass?.provenance?.some((entry: Provenance) => entry.source === 'ratings-v4-derived')}
          <small>Source: Ratings V4 derived catalogue</small>
        {/if}
      </div>
    {/if}
    <svg
      class="galaxy-label-leaders"
      aria-hidden="true"
      data-atlas-region-leader-count={atlasRegionLabels.length}
    >
      {#each atlasRegionLabels as label (label.key)}
        <polyline
          class:is-selected={label.selected}
          class:is-hovered={label.hovered}
          points={`${label.anchorXPx},${label.anchorYPx} ${label.leaderEndXPx},${label.yPx}`}
        ></polyline>
        <circle
          class:is-selected={label.selected}
          class:is-hovered={label.hovered}
          cx={label.anchorXPx}
          cy={label.anchorYPx}
          r={label.selected || label.hovered ? 2.5 : 1.35}
        ></circle>
      {/each}
    </svg>
    <div
      class="galaxy-label-layer"
      class:labels-moving={cameraTransitionActive}
      data-visible-label-count={visibleGalaxyLabels.length}
    >
      {#each renderedGalaxyLabels as label (label.key)}
        {#if label.kind === 'system' && label.point}
          <button
            type="button"
            class="galaxy-label galaxy-label--system"
            class:is-selected={label.selected}
            class:is-hovered={label.hovered}
            data-map-interactive
            data-system-id64={label.point.systemId64}
            data-map-label-kind={label.kind}
            data-map-label-key={label.key}
            data-map-label-selected={label.selected || undefined}
            data-map-label-anchor-x={label.anchorXPx}
            data-map-label-anchor-y={label.anchorYPx}
            aria-label={`Select ${label.text}`}
            aria-describedby={`${systemKeyboardHelpId}${hoveredSystemId64 === label.point.systemId64 ? ` ${tooltipId}` : ''}`}
            aria-keyshortcuts="ArrowRight"
            onkeydown={(event) => systemKey(event, label.point!)}
            style:left={`${label.xPx}px`}
            style:top={`${label.yPx}px`}
            onpointerenter={(event) => showSystem(label.point!, event)}
            onpointerleave={scheduleHoverClear}
            onfocus={(event) => showSystem(label.point!, event)}
            onblur={scheduleHoverClear}
            onclick={() => selectSystem(label.point!)}
          >
            <span class="galaxy-label__beacon" aria-hidden="true"></span>
            <span class="galaxy-label__name">{label.text}</span>
          </button>
        {:else}
          <span
            aria-hidden="true"
            class="galaxy-label galaxy-label--region"
            class:galaxy-label--atlas={label.placement !== 'map'}
            class:galaxy-label--atlas-left={label.placement === 'atlas-left'}
            class:galaxy-label--atlas-right={label.placement === 'atlas-right'}
            class:is-selected={label.selected}
            class:is-hovered={label.hovered}
            class:is-current={label.current}
            class:is-muted={label.muted}
            data-map-label-kind={label.kind}
            data-map-label-key={label.key}
            data-map-label-selected={label.selected || undefined}
            data-map-label-muted={label.muted || undefined}
            data-map-label-anchor-x={label.anchorXPx}
            data-map-label-anchor-y={label.anchorYPx}
            style:left={`${label.xPx}px`}
            style:top={`${label.yPx}px`}>{label.text}</span
          >
        {/if}
      {/each}
    </div>
    {#if systemLabels.length > 0}
      <div
        class="system-label-layer"
        aria-hidden="true"
        data-system-label-count={systemLabels.length}
      >
        {#each systemLabels as label (label.bodyId)}
          <span
            class="system-label system-label--{label.kind}"
            class:is-selected={label.selected}
            class:is-left={label.side === 'left'}
            data-system-label-body-id={label.bodyId}
            style:left={`${label.x}px`}
            style:top={`${label.y}px`}>{label.text}</span
          >
        {/each}
      </div>
    {/if}
  </div>
  {#if canGalaxyNavigate}
    <details
      class="nearby-systems"
      class:nearby-systems--overlay={controlsOverlay}
      bind:open={nearbyOpen}
    >
      <summary>Nearby systems ({nearbySystems.length})</summary>
      {#if nearbyOpen}
        {#if nearbySystems.length}
          <ul aria-label="Nearby systems">
            {#each nearbySystems as candidate (candidate.key)}
              <li>
                <button
                  type="button"
                  data-system-id64={candidate.point!.systemId64}
                  aria-describedby={`${systemKeyboardHelpId}${hoveredSystemId64 === candidate.point!.systemId64 ? ` ${tooltipId}` : ''}`}
                  aria-keyshortcuts="ArrowRight"
                  onkeydown={(event) => systemKey(event, candidate.point!)}
                  onfocus={(event) => showSystem(candidate.point!, event)}
                  onblur={scheduleHoverClear}
                  onclick={() => selectSystem(candidate.point!)}
                  >{candidate.text}</button
                >
              </li>
            {/each}
          </ul>
        {:else}
          <p>Zoom in or search to find nearby systems.</p>
        {/if}
      {/if}
    </details>
  {/if}
  {#if controlsOverlay}
    <!-- Floats over the bottom of the map. The bar itself lets drags through
         to the map; only its controls take the pointer. -->
    <div class="map-overlay-bar" {@attach forwardMapWheel}>
      {#if canGalaxyNavigate}
        {@render galaxyCameraControls()}
        {#if !cameraActions}
          <details class="camera-help">
            <summary>Map controls</summary>{@render galaxyCameraHelp()}
          </details>
        {/if}
      {/if}
      {@render rendererStatus()}
    </div>
  {/if}
  {#if hoveredSystem && !labelsMoving}
    <div
      class="system-hover-position"
      bind:this={tooltipHost}
      role="presentation"
      onpointerenter={keepHover}
      onpointerleave={scheduleHoverClear}
      onfocusin={keepHover}
      onfocusout={scheduleHoverClear}
      style:left={`${cardPosition.x}px`}
      style:top={`${cardPosition.y}px`}
    >
      <GalaxySystemHoverCard
        id={tooltipId}
        system={hoveredSystem}
        anchor={anchorPoint}
        onSelect={selectSystem}
        inspectHref={systemInspectHref?.(hoveredSystem) ??
          resolve(
            `/inspect?system=${encodeURIComponent(hoveredSystem.systemId64)}`,
          )}
        onInspect={onSystemInspect}
        onDismiss={dismissHover}
      />
    </div>
  {/if}
</div>
{#if scene?.kind === 'system'}
  <SystemBodyLegend bodies={scene.bodies} />
{/if}
{#if !controlsOverlay}
  {@render rendererStatus()}
{/if}

<style>
  .system-keyboard-help {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .galaxy-label__name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .map-systems-surface {
    position: relative;
  }
  .system-hover-position {
    position: absolute;
    z-index: 5;
    max-width: calc(100% - 16px);
  }
  .nearby-systems {
    margin-top: 0.75rem;
    color: var(--text);
  }
  .nearby-systems summary {
    cursor: pointer;
    padding: 0.4rem 0;
  }
  .nearby-systems ul {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0;
    list-style: none;
  }
  .nearby-systems button {
    border: 1px solid var(--line);
    border-radius: 0.4rem;
    background: var(--panel-raised);
    color: var(--text);
    padding: 0.4rem 0.7rem;
    cursor: pointer;
  }
  .nearby-systems button:focus-visible,
  .nearby-systems summary:focus-visible,
  .galaxy-label--system:focus-visible {
    outline: 2px solid var(--neon-hi);
    outline-offset: 2px;
  }
  .labels-moving {
    visibility: hidden;
  }

  .system-body-hover {
    position: absolute;
    /* Above the name overlay (z-index 2): the card must stay readable when a
       body's name is moved under it. */
    z-index: 3;
    top: 0.75rem;
    right: 0.75rem;
    display: grid;
    gap: 0.25rem;
    max-width: min(20rem, calc(100% - 1.5rem));
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--panel) 93%, transparent);
    color: var(--text);
    font-size: 0.8rem;
    pointer-events: none;
  }
  .system-body-hover span,
  .system-body-hover small {
    color: var(--text-muted);
  }
  .spatial-canvas {
    position: relative;
    width: 100%;
    height: clamp(32rem, 76vh, 68rem);
    overflow: hidden;
    border: 1px solid var(--line);
    background: var(--bg-void);
    touch-action: none;
    cursor: grab;
    border-radius: var(--radius-lg);
  }

  .spatial-canvas:active {
    cursor: grabbing;
  }
  .spatial-canvas:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 3px;
  }
  .camera-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
  }
  .camera-toolbar button {
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    padding: 0.45rem 0.75rem;
    color: var(--text);
    cursor: pointer;
  }
  .camera-angle {
    display: inline-flex;
  }
  .camera-angle button {
    border-radius: 0;
  }
  .camera-angle button:first-child {
    border-radius: var(--radius-pill) 0 0 var(--radius-pill);
  }
  .camera-angle button:last-child {
    border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
  }
  .camera-angle button[aria-pressed='true'] {
    border-color: var(--neon);
    background: var(--neon-soft);
  }
  .map-overlay-bar {
    position: absolute;
    z-index: 4;
    right: 0.5rem;
    bottom: 0.5rem;
    left: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 0.3rem 0.5rem;
    pointer-events: none;
  }
  .map-overlay-bar > :global(*) {
    pointer-events: auto;
  }
  /* No backdrop-filter here: it would become the containing block for the
     fixed-position Sources pop-out inside this bar and misplace it. */
  .camera-toolbar--overlay {
    flex: 0 1 auto;
    min-width: 0;
    gap: 0.2rem;
    padding: 0.25rem 0.4rem;
    border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--panel) 84%, transparent);
  }
  .camera-toolbar--overlay button {
    min-height: 1.9rem;
    padding: 0.25rem 0.45rem;
    background: color-mix(in srgb, var(--panel-raised) 82%, transparent);
    font-size: 0.75rem;
  }
  .camera-toolbar--overlay .camera-distance {
    margin-left: 0.4rem;
    justify-content: flex-start;
    font-size: 0.7rem;
    gap: 0.2rem 0.6rem;
  }
  /* Qualified by the bar so it outranks the page-wide map-panel status style,
     and pushed to the right even when the bar wraps onto a second row. */
  .map-overlay-bar .renderer-status--overlay {
    min-height: 0;
    margin: 0 0 0 auto;
    padding: 0.1rem 0.45rem;
    border-radius: var(--radius-pill);
    background: color-mix(in srgb, var(--panel) 84%, transparent);
    color: var(--text-dim);
    font-size: 0.62rem;
  }
  .nearby-systems--overlay {
    position: absolute;
    z-index: 4;
    top: 0.5rem;
    right: 0.5rem;
    max-width: min(20rem, calc(100% - 1rem));
    max-height: 45%;
    overflow: auto;
    margin: 0;
    padding: 0.1rem 0.6rem;
    border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--panel) 84%, transparent);
    font-size: 0.75rem;
  }
  .nearby-systems--overlay summary {
    padding: 0.25rem 0;
  }
  .camera-toolbar button:hover:not(:disabled) {
    background: var(--panel-raised);
    border-color: var(--line-strong);
    box-shadow: var(--line-glow);
  }
  .camera-toolbar button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .camera-distance {
    margin-left: auto;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 0.25rem 0.7rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
  }
  .camera-distance span:last-child {
    color: var(--text-dim);
  }
  .camera-help {
    color: var(--text-muted);
    font-size: 0.75rem;
    margin: 0.6rem 0;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
    touch-action: none;
  }

  .galaxy-label-layer {
    position: absolute;
    z-index: 2;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
  }
  .system-label-layer {
    position: absolute;
    z-index: 2;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
  }
  /* Positions follow the camera exactly, so there is no easing here: a name
     that trails its body during an orbit drag reads as the wrong body. */
  .system-label {
    position: absolute;
    max-width: 16rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    transform: translate(0, -50%);
    color: color-mix(in srgb, var(--text) 82%, transparent);
    font-size: 0.7rem;
    font-weight: 560;
    line-height: 1.2;
    letter-spacing: 0.02em;
    text-shadow:
      0 1px 2px var(--bg-void),
      0 0 5px var(--bg-void),
      0 0 12px var(--bg-void);
  }
  .system-label.is-left {
    transform: translate(-100%, -50%);
    text-align: right;
  }
  .system-label--star {
    color: var(--text);
    font-size: 0.76rem;
    font-weight: 680;
  }
  .system-label--moon,
  .system-label--other {
    color: color-mix(in srgb, var(--text-muted) 88%, transparent);
    font-size: 0.64rem;
    font-weight: 520;
  }
  .system-label.is-selected {
    color: var(--accent-hi);
    font-weight: 720;
  }
  .galaxy-label-leaders {
    position: absolute;
    z-index: 1;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    pointer-events: none;
  }
  .galaxy-label-leaders polyline {
    fill: none;
    stroke: color-mix(in srgb, var(--neon) 20%, transparent);
    stroke-width: 0.75;
    vector-effect: non-scaling-stroke;
  }
  .galaxy-label-leaders circle {
    fill: color-mix(in srgb, var(--neon) 62%, transparent);
  }
  .galaxy-label-leaders .is-hovered {
    stroke: color-mix(in srgb, var(--neon-hi) 78%, transparent);
    fill: color-mix(in srgb, var(--neon-hi) 96%, transparent);
  }
  .galaxy-label-leaders .is-selected {
    stroke: color-mix(in srgb, var(--accent-hi) 66%, transparent);
    fill: color-mix(in srgb, var(--accent-hi) 92%, transparent);
  }
  .galaxy-label {
    position: absolute;
    display: inline-flex;
    align-items: center;
    max-width: 14rem;
    transform: translate(-50%, -50%);
    transform-origin: center;
    text-align: center;
    text-wrap: balance;
    line-height: 1.18;
  }
  /* A name sits exactly on its point and follows the camera with no easing,
     like the System Map names: one that trails its region reads as the wrong
     region. Region names never jump aside to make room; a name that would
     overlap a more important one fades instead, and only that fade eases. */
  .galaxy-label--region {
    z-index: 1;
    transition: opacity 220ms ease-out;
    justify-content: center;
    color: color-mix(in srgb, var(--text-muted) 80%, transparent);
    font-size: 0.63rem;
    font-weight: 620;
    letter-spacing: 0.055em;
    text-transform: uppercase;
    text-shadow:
      0 1px 2px var(--bg-void),
      0 0 6px var(--bg-void),
      0 0 14px color-mix(in srgb, var(--neon) 26%, transparent);
  }
  /* A faded name sits under the names at full strength and drops its dark
     halo, so it never smudges the name it overlaps. */
  .galaxy-label--region.is-muted {
    z-index: 0;
    opacity: 0.28;
    text-shadow: none;
  }
  .galaxy-label--region.is-hovered {
    color: var(--neon-hi);
    font-weight: 720;
    text-shadow:
      0 1px 3px var(--bg-void),
      0 0 8px var(--bg-void),
      0 0 17px color-mix(in srgb, var(--neon-hi) 52%, transparent);
  }
  .galaxy-label--region.is-current,
  .galaxy-label--region.is-selected {
    color: var(--accent-hi);
    font-weight: 720;
    text-shadow:
      0 1px 3px var(--bg-void),
      0 0 8px var(--bg-void),
      0 0 15px color-mix(in srgb, var(--accent) 38%, transparent);
  }
  .galaxy-label--region.galaxy-label--atlas {
    width: min(13.8rem, 21vw);
    max-width: 13.8rem;
    color: color-mix(in srgb, var(--text-muted) 78%, transparent);
    font-size: clamp(0.5rem, 1.1vh, 0.61rem);
    line-height: 1;
    white-space: nowrap;
    text-wrap: nowrap;
  }
  /* The atlas rule follows the generic state rules in source order, so repeat
     the interactive states with the atlas qualifier. Hover/selection must be
     conspicuous without removing or dimming any of the other 41 names. */
  .galaxy-label--region.galaxy-label--atlas.is-hovered {
    color: var(--neon-hi);
    font-weight: 760;
    text-shadow:
      0 1px 3px var(--bg-void),
      0 0 9px var(--bg-void),
      0 0 19px color-mix(in srgb, var(--neon-hi) 82%, transparent);
  }
  .galaxy-label--region.galaxy-label--atlas.is-current,
  .galaxy-label--region.galaxy-label--atlas.is-selected {
    color: var(--accent-hi);
    font-weight: 760;
    text-shadow:
      0 1px 3px var(--bg-void),
      0 0 9px var(--bg-void),
      0 0 18px color-mix(in srgb, var(--accent) 68%, transparent);
  }
  .galaxy-label--atlas-left {
    justify-content: flex-end;
    text-align: right;
  }
  .galaxy-label--atlas-right {
    justify-content: flex-start;
    text-align: left;
  }
  @media (prefers-reduced-motion: reduce) {
    .galaxy-label--region {
      transition: none;
    }
  }
  .galaxy-label--system {
    pointer-events: auto;
    cursor: pointer;
    font-family: inherit;
    gap: 0.38rem;
    padding: 0.28rem 0.52rem 0.3rem;
    border: 1px solid color-mix(in srgb, var(--neon) 34%, transparent);
    border-radius: var(--radius-sm);
    color: var(--text);
    background: linear-gradient(
      120deg,
      color-mix(in srgb, var(--panel-sunken) 94%, transparent),
      color-mix(in srgb, var(--bg-void) 82%, transparent)
    );
    box-shadow: var(--shadow-panel);
    font:
      650 0.68rem ui-monospace,
      SFMono-Regular,
      Consolas,
      monospace;
    letter-spacing: 0.025em;
    white-space: nowrap;
  }
  .galaxy-label--system.is-selected {
    border-color: color-mix(in srgb, var(--accent-hi) 80%, transparent);
    color: var(--text);
    background: linear-gradient(
      120deg,
      color-mix(in srgb, var(--accent-text) 94%, transparent),
      color-mix(in srgb, var(--panel) 90%, transparent)
    );
    box-shadow:
      0 5px 20px color-mix(in srgb, var(--bg-void) 50%, transparent),
      0 0 18px color-mix(in srgb, var(--accent) 18%, transparent),
      inset 0 1px color-mix(in srgb, var(--accent-hi) 8%, transparent);
  }
  .galaxy-label__beacon {
    width: 0.35rem;
    height: 0.35rem;
    flex: 0 0 auto;
    border-radius: var(--radius-pill);
    background: var(--neon-hi);
    box-shadow: 0 0 8px color-mix(in srgb, var(--neon-hi) 86%, transparent);
  }
  .is-selected .galaxy-label__beacon {
    background: var(--accent-hi);
    box-shadow: 0 0 9px color-mix(in srgb, var(--accent) 92%, transparent);
  }

  .renderer-status {
    min-height: 1.5rem;
    margin: 0.75rem 0 0;
    color: var(--text-muted);
  }
</style>
