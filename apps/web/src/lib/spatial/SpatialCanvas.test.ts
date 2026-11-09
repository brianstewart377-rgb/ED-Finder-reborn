import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import axe from 'axe-core';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SpatialCanvas from './SpatialCanvas.svelte';
import {
  buildGalaxyReviewFixtureScene,
  galaxyReviewDensity,
} from './galaxy-review-fixture';
import * as density from './galaxy-density';
import {
  elevateGalaxyCameraByPixels,
  GALAXY_NAVIGATION_CENTRE_LY,
  orbitGalaxyCamera,
  panGalaxyCamera,
} from './galaxy-camera';
import * as galaxyLabels from './galaxy-labels';
import { projectGalaxyLabel } from './galaxy-labels';
import { systemDetailFromV3 } from '$lib/api/client';
import { v3Body, v3System } from '$lib/api/v3-system-fixtures';
import {
  buildSystemScene,
  SYSTEM_OVERVIEW_REFERENCE_ASPECT,
} from './system-scene';
import { solLikeSystemDetail } from './system-scene-fixtures';
import type {
  CameraState,
  GalaxySceneContract,
  GalaxySystemPoint,
  RuntimeEventListener,
  SpatialSceneContract,
} from './contracts';

const adapter = vi.hoisted(() => ({
  runtimes: [] as Array<{
    dispatch: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
    subscribe: ReturnType<typeof vi.fn>;
    unsubscribe: ReturnType<typeof vi.fn>;
    emit: (event: Parameters<RuntimeEventListener>[0]) => void;
  }>,
  create: vi.fn(),
}));

const feature = vi.hoisted(() => ({ finder: false }));

vi.mock('$lib/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('$lib/config')>()),
  get FINDER_F4_ENABLED() {
    return feature.finder;
  },
}));

vi.mock('./babylon/adapter', () => ({
  createBabylonSpatialRuntime: adapter.create,
}));

let observerCallback: ResizeObserverCallback;
const observe = vi.fn();
const disconnect = vi.fn();

class ResizeObserverMock {
  constructor(callback: ResizeObserverCallback) {
    observerCallback = callback;
  }

  observe = observe;
  disconnect = disconnect;
  unobserve = vi.fn();
}

const catalogueStar: GalaxySystemPoint = {
  systemId64: '9007199254740993',
  name: 'Catalogue Vega',
  positionLy: { x: 0, y: 0, z: 0 },
  primaryStar: { type: 'G2' },
};
const overlappingStar: GalaxySystemPoint = {
  systemId64: '9007199254740994',
  name: 'Nearby companion',
  positionLy: { x: 2, y: 0, z: 0 },
  primaryStar: { type: 'M4' },
};
const finderStar: GalaxySystemPoint = {
  systemId64: '41',
  name: 'Finder Reach',
  positionLy: { x: -180, y: 0, z: 0 },
};

function nearbyScene(): GalaxySceneContract {
  return {
    kind: 'galaxy',
    revision: 1,
    camera: {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 500,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision: 1,
    },
    selection: [],
    contributions: [
      {
        id: 'nearby-catalogue',
        owner: 'CATALOGUE',
        revision: 1,
        layers: [
          {
            id: 'catalogue-systems',
            version: 1,
            representation: 'AUTHORITATIVE',
            payload: { systems: [catalogueStar, overlappingStar] },
            targetCount: 2,
            truncated: false,
          },
        ],
      },
      {
        id: 'nearby-finder',
        owner: 'FINDER',
        revision: 1,
        layers: [
          {
            id: 'finder-systems',
            version: 1,
            representation: 'AUTHORITATIVE',
            payload: { systems: [finderStar] },
            targetCount: 1,
            truncated: false,
          },
        ],
      },
    ],
  };
}

function pointer(
  target: EventTarget,
  type: string,
  pointerId: number,
  x: number,
  y: number,
  options: PointerEventInit = {},
): void {
  target.dispatchEvent(
    new PointerEvent(type, {
      pointerId,
      pointerType: 'touch',
      button: 0,
      clientX: x,
      clientY: y,
      bubbles: true,
      ...options,
    }),
  );
}

function lastGalaxyCamera(): CameraState {
  const command = adapter.runtimes[0]!.dispatch.mock.calls.findLast(
    ([command]) => command.type === 'SET_CAMERA',
  )?.[0];
  expect(command).toBeDefined();
  return command.camera;
}

describe('SpatialCanvas', () => {
  beforeEach(() => {
    feature.finder = false;
    adapter.runtimes.length = 0;
    adapter.create.mockReset();
    observe.mockReset();
    disconnect.mockReset();
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(360);
    adapter.create.mockImplementation((_canvas, onStatus) => {
      let listener: RuntimeEventListener = () => undefined;
      const record = {
        dispatch: vi.fn(() => ({ status: 'executed' as const })),
        dispose: vi.fn(),
        subscribe: vi.fn((next: RuntimeEventListener) => {
          listener = next;
          return record.unsubscribe;
        }),
        unsubscribe: vi.fn(),
        emit: (event: Parameters<RuntimeEventListener>[0]) => listener(event),
      };
      adapter.runtimes.push(record);
      return {
        getStatus: () => ({ state: 'created' as const }),
        start: vi.fn(async () => {
          onStatus({ state: 'starting' });
          const ready = { state: 'ready' as const, backend: 'WEBGL2' as const };
          onStatus(ready);
          return ready;
        }),
        dispatch: record.dispatch,
        subscribe: record.subscribe,
        resize: vi.fn(),
        dispose: record.dispose,
      };
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('publishes accessible readiness and forwards observed dimensions', async () => {
    const onViewportChange = vi.fn();
    render(SpatialCanvas, { props: { onViewportChange } });

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Renderer ready (WEBGL2)',
    );
    expect(document.querySelector('[data-spatial-canvas]')).toBeTruthy();
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'RESIZE',
      width: 640,
      height: 360,
      dpr: 1,
    });
    expect(onViewportChange).toHaveBeenCalledWith({ width: 640, height: 360 });

    observerCallback(
      [
        {
          contentRect: { width: 800.4, height: 450.2 },
        } as unknown as ResizeObserverEntry,
      ],
      {} as ResizeObserver,
    );
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenLastCalledWith({
      type: 'RESIZE',
      width: 800,
      height: 450,
      dpr: 1,
    });
    expect(onViewportChange).toHaveBeenLastCalledWith({
      width: 800,
      height: 450,
    });
  });

  it('disconnects observation and disposes each runtime across remounts', async () => {
    const first = render(SpatialCanvas);
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    first.unmount();

    expect(disconnect).toHaveBeenCalledOnce();
    expect(adapter.runtimes[0]?.dispose).toHaveBeenCalledOnce();
    expect(adapter.runtimes[0]?.subscribe).toHaveBeenCalledOnce();
    expect(adapter.runtimes[0]?.unsubscribe).toHaveBeenCalledOnce();

    const second = render(SpatialCanvas);
    await waitFor(() => expect(adapter.runtimes).toHaveLength(2));
    second.unmount();

    expect(disconnect).toHaveBeenCalledTimes(2);
    expect(adapter.runtimes[1]?.dispose).toHaveBeenCalledOnce();
    expect(adapter.runtimes[1]?.subscribe).toHaveBeenCalledOnce();
    expect(adapter.runtimes[1]?.unsubscribe).toHaveBeenCalledOnce();
  });

  it.each([false, true])(
    'leaves no hover dismissal timer after unmount (pending hover: %s)',
    async (pendingHover) => {
      const view = render(SpatialCanvas);
      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveAttribute(
          'data-renderer-state',
          'ready',
        ),
      );
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
      if (pendingHover) {
        view.container
          .querySelector('.spatial-canvas')!
          .dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
        // Flush Svelte's delegated-event cleanup; keep the delayed hover work.
        await vi.advanceTimersByTimeAsync(0);
      }
      expect(vi.getTimerCount()).toBe(pendingHover ? 1 : 0);

      view.unmount();
      expect(adapter.runtimes[0]?.dispose).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);
      adapter.runtimes[0]!.dispatch.mockClear();
      await vi.advanceTimersByTimeAsync(1_000);
      expect(adapter.runtimes[0]?.dispatch).not.toHaveBeenCalled();
    },
  );

  it('loads product scenes, focuses targets, and forwards neutral picks from the stable host', async () => {
    const scene: SpatialSceneContract = {
      kind: 'galaxy',
      revision: 9,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 30,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 9,
      },
      selection: [],
      contributions: [],
    };
    const eventHandler = vi.fn();
    const focusTarget = { kind: 'system' as const, systemId64: '42' };
    const view = render(SpatialCanvas, {
      props: {
        scene,
        focusTarget,
        focusRevision: 1,
        onRuntimeEvent: eventHandler,
      },
    });
    await waitFor(() =>
      expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
        type: 'LOAD_SCENE',
        scene,
      }),
    );
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'FLY_TO',
      target: focusTarget,
      reducedMotion: true,
    });

    const host = view.container.querySelector('.spatial-canvas');
    expect(host).toBeTruthy();
    await host?.dispatchEvent(
      new PointerEvent('pointerdown', {
        button: 0,
        clientX: 12,
        clientY: 18,
        bubbles: true,
      }),
    );
    await host?.dispatchEvent(
      new PointerEvent('pointerup', {
        button: 0,
        clientX: 12,
        clientY: 18,
        bubbles: true,
      }),
    );
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'PICK',
      screenX: 12,
      screenY: 18,
    });

    const picked = {
      type: 'TARGET_PICKED' as const,
      target: { kind: 'system' as const, systemId64: '42' },
    };
    adapter.runtimes[0]?.emit(picked);
    expect(eventHandler).toHaveBeenCalledWith(picked);
    await waitFor(() =>
      expect(host).toHaveAttribute('data-last-picked-id64', '42'),
    );

    adapter.runtimes[0]?.emit({
      type: 'SCENE_APPLIED',
      sceneRevision: 9,
      renderedLayers: [
        {
          contributionId: 'catalogue-density-base',
          contributionRevision: 9,
          layerId: 'catalogue-density',
          layerVersion: 1,
          representation: 'DERIVED',
          acceptedTargetCount: 3,
          renderedTargetCount: 3,
          sourceGeneration: 'fixture:density-v1',
        },
      ],
    });
    await waitFor(() => {
      expect(host).toHaveAttribute('data-applied-scene-revision', '9');
      expect(host).toHaveAttribute(
        'data-rendered-layer-ids',
        'catalogue-density',
      );
    });
  });

  it('forwards a contribution refresh after the scene is ready and mirrors its receipt', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const contributionPatch = {
      ...scene.contributions[0]!,
      revision: scene.contributions[0]!.revision + 1,
    };
    const view = render(SpatialCanvas, {
      props: {
        scene,
        contributionPatch,
        contributionPatchRevision: 1,
      },
    });
    const host = view.container.querySelector('.spatial-canvas');

    await waitFor(() =>
      expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
        type: 'PATCH_CONTRIBUTION',
        contribution: contributionPatch,
      }),
    );
    adapter.runtimes[0]?.emit({
      type: 'CONTRIBUTION_APPLIED',
      contributionId: contributionPatch.id,
      contributionRevision: contributionPatch.revision,
      renderedLayers: [
        {
          contributionId: contributionPatch.id,
          contributionRevision: contributionPatch.revision,
          layerId: 'catalogue-density',
          layerVersion: 1,
          representation: 'DERIVED',
          acceptedTargetCount: 20,
          renderedTargetCount: 20,
          sourceGeneration: 'fixture:galaxy-review-v1',
        },
      ],
    });

    await waitFor(() => {
      expect(host).toHaveAttribute('data-applied-contribution-revision', '2');
      expect(host).toHaveAttribute(
        'data-rendered-layer-ids',
        'catalogue-density',
      );
    });
  });

  it('throttles mouse hover, clears it on leave, and mirrors region events', async () => {
    let hoverCallback: FrameRequestCallback | undefined;
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        hoverCallback = callback;
        return 91;
      }),
    );
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const eventHandler = vi.fn();
    const view = render(SpatialCanvas, {
      props: { onRuntimeEvent: eventHandler },
    });
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const host = view.container.querySelector('.spatial-canvas');
    const canvas = view.container.querySelector('canvas');
    expect(host).toBeTruthy();
    expect(canvas).toBeTruthy();
    vi.spyOn(canvas!, 'getBoundingClientRect').mockReturnValue({
      left: 10,
      top: 20,
      right: 650,
      bottom: 380,
      width: 640,
      height: 360,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    });

    host?.dispatchEvent(
      new PointerEvent('pointermove', {
        pointerType: 'mouse',
        clientX: 34,
        clientY: 58,
        bubbles: true,
      }),
    );
    host?.dispatchEvent(
      new PointerEvent('pointermove', {
        pointerType: 'mouse',
        clientX: 46,
        clientY: 72,
        bubbles: true,
      }),
    );
    expect(requestAnimationFrame).toHaveBeenCalledOnce();
    hoverCallback?.(0);
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'HOVER',
      screenX: 36,
      screenY: 52,
    });

    const hovered = {
      type: 'TARGET_HOVERED' as const,
      target: { kind: 'region' as const, id: '18' },
    };
    adapter.runtimes[0]?.emit(hovered);
    expect(eventHandler).toHaveBeenCalledWith(hovered);
    await waitFor(() =>
      expect(host).toHaveAttribute('data-last-hovered-region-id', '18'),
    );

    host?.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'CLEAR_HOVER',
    });

    adapter.runtimes[0]?.emit({
      type: 'TARGET_PICKED',
      target: { kind: 'region', id: '18' },
    });
    await waitFor(() =>
      expect(host).toHaveAttribute('data-last-picked-region-id', '18'),
    );
  });

  it('lets keyboard users pan, zoom and rotate with the same neutral camera command', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Zoom in' })).toBeVisible(),
    );
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const host = view.container.querySelector('.spatial-canvas')!;
    const runtime = adapter.runtimes[0]!;
    host.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
    );
    await waitFor(() =>
      expect(runtime.dispatch).toHaveBeenCalledWith({
        type: 'SET_CAMERA',
        camera: expect.objectContaining({
          focusLy: expect.objectContaining({ x: expect.any(Number) }),
        }),
      }),
    );
    const pan = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(pan.camera.focusLy.x).toBeGreaterThan(scene.camera.focusLy.x);
    expect(pan.camera.focusLy.y).toBe(scene.camera.focusLy.y);
    host.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'e', bubbles: true }),
    );
    const rotated = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(rotated.camera.bearingRad).toBeCloseTo(0.12);
    screen.getByRole('button', { name: 'Zoom in' }).click();
    const zoomed = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(zoomed.camera.distanceLy).toBeLessThan(scene.camera.distanceLy);
    screen.getByRole('button', { name: 'Top down' }).click();
    const top = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(top.camera.pitchRad).toBe(Math.PI / 2);
    expect(top.transition).toEqual({ durationMs: 650, reducedMotion: true });
  });

  it('steps elevation with Up/Down, shows rounded coordinates and resets to the anchored Y', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: {
        ...fixture.camera,
        focusLy: { x: 12.6, y: 37.4, z: -55.7 },
        distanceLy: 1_000,
      },
    };
    render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    expect(screen.getByLabelText('Focus coordinates')).toHaveTextContent(
      'X 13 / Y 37 / Z -56 LY',
    );
    expect(
      screen.getByText(/Alt\/Option-drag to move up\/down/),
    ).toHaveTextContent('Scroll or pinch to zoom · Two-finger drag to pan');
    const up = screen.getByRole('button', { name: 'Move up' });
    const down = screen.getByRole('button', { name: 'Move down' });
    expect(up).toHaveTextContent('Up');
    expect(down).toHaveTextContent('Down');
    up.click();
    expect(lastGalaxyCamera()).toMatchObject({
      focusLy: { x: 12.6, y: 137.4, z: -55.7 },
      distanceLy: 1_000,
      pitchRad: scene.camera.pitchRad,
      bearingRad: scene.camera.bearingRad,
    });
    await waitFor(() =>
      expect(screen.getByLabelText('Focus coordinates')).toHaveTextContent(
        'X 13 / Y 137 / Z -56 LY',
      ),
    );
    down.click();
    expect(lastGalaxyCamera().focusLy.y).toBeCloseTo(37.4);
    screen.getByRole('button', { name: 'Zoom out' }).click();
    const range = lastGalaxyCamera().distanceLy;
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBeCloseTo(37.4 + range * 0.1);

    screen.getByRole('button', { name: 'Top down' }).click();
    expect(lastGalaxyCamera().pitchRad).toBe(Math.PI / 2);
    expect(lastGalaxyCamera().focusLy.y).toBeCloseTo(37.4 + range * 0.1);
    screen.getByRole('button', { name: 'Tilted' }).click();
    expect(lastGalaxyCamera().pitchRad).toBe(0.55);
    expect(lastGalaxyCamera().focusLy.y).toBeCloseTo(37.4 + range * 0.1);
    screen.getByRole('button', { name: 'Reset view' }).click();
    expect(lastGalaxyCamera()).toEqual({
      ...scene.camera,
      revision: lastGalaxyCamera().revision,
    });
    await waitFor(() =>
      expect(screen.getByLabelText('Focus coordinates')).toHaveTextContent(
        'X 13 / Y 37 / Z -56 LY',
      ),
    );
  });

  it('clamps elevation controls to the catalogue bounds', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: {
        ...fixture.camera,
        focusLy: { x: 12, y: 890, z: -55 },
        distanceLy: 20_000,
      },
    };
    render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    screen.getByRole('button', { name: 'Move up' }).click();
    expect(lastGalaxyCamera().focusLy).toEqual({ x: 12, y: 900, z: -55 });
    screen.getByRole('button', { name: 'Move down' }).click();
    expect(lastGalaxyCamera().focusLy).toEqual({ x: 12, y: -900, z: -55 });
  });

  it('retains complete catalogue bounds across partial refreshes without treating partial samples as bounds', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const partialPayload = {
      ...galaxyReviewDensity,
      complete: false,
      cells: [galaxyReviewDensity.cells[0]!],
      coveredSystemCount: galaxyReviewDensity.cells[0]!.systemCount,
      boundsLy: {
        min: { ...galaxyReviewDensity.boundsLy.min, y: -100 },
        max: { ...galaxyReviewDensity.boundsLy.max, y: 100 },
      },
    };
    const scene = {
      ...fixture,
      camera: {
        ...fixture.camera,
        distanceLy: 20_000,
      },
      contributions: [
        density.createCatalogueDensityContribution(partialPayload, 1),
      ],
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const up = screen.getByRole('button', { name: 'Move up' });
    const down = screen.getByRole('button', { name: 'Move down' });
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(2_000);

    const complete = density.createCatalogueDensityContribution(
      galaxyReviewDensity,
      2,
    );
    await view.rerender({
      contributionPatch: complete,
      contributionPatchRevision: 1,
    });
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(900);

    await view.rerender({
      contributionPatch: density.createCatalogueDensityContribution(
        partialPayload,
        3,
      ),
      contributionPatchRevision: 2,
    });
    down.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(-900);

    await view.rerender({
      contributionPatch: density.createCatalogueDensityContribution(
        { ...partialPayload, generationId: 'fixture:new-generation' },
        4,
      ),
      contributionPatchRevision: 3,
    });
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(1_100);
  });

  it('uses complete bounds from the first density patch into an empty F4 contribution', async () => {
    feature.finder = true;
    const fixture = buildGalaxyReviewFixtureScene();
    const complete = density.createCatalogueDensityContribution(
      galaxyReviewDensity,
      1,
    );
    const scene = {
      ...fixture,
      camera: { ...fixture.camera, distanceLy: 20_000 },
      contributions: [{ ...complete, revision: 0, layers: [] }],
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const up = screen.getByRole('button', { name: 'Move up' });
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(2_000);
    await view.rerender({
      contributionPatch: complete,
      contributionPatchRevision: 1,
    });
    up.click();
    expect(lastGalaxyCamera().focusLy.y).toBe(900);
    expect(adapter.runtimes[0]!.dispatch).toHaveBeenCalledWith({
      type: 'PATCH_CONTRIBUTION',
      contribution: complete,
    });
  });

  it('does not revalidate catalogue cells when only the scene camera changes', async () => {
    const validate = vi.spyOn(density, 'catalogueDensitySceneLayer');
    const scene = buildGalaxyReviewFixtureScene();
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    expect(validate).toHaveBeenCalledOnce();
    await view.rerender({
      scene: {
        ...scene,
        revision: 2,
        camera: {
          ...scene.camera,
          focusLy: { x: 100, y: 30, z: 60 },
          revision: 2,
        },
      },
    });
    expect(validate).toHaveBeenCalledOnce();
  });

  it('widens a system overview for a narrow canvas and resets to that fit', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600);
    const scene: SpatialSceneContract = {
      kind: 'system',
      revision: 4,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 153,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 4,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    render(SpatialCanvas, { props: { scene } });
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const runtime = adapter.runtimes[0]!;
    const expected = Math.ceil(153 * SYSTEM_OVERVIEW_REFERENCE_ASPECT);
    await waitFor(() => {
      const fitted = runtime.dispatch.mock.calls.findLast(
        ([command]) => command.type === 'SET_CAMERA',
      )?.[0];
      expect(fitted?.camera.semanticDistance).toBe(expected);
    });
    screen.getByRole('button', { name: 'Zoom in' }).click();
    screen.getByRole('button', { name: 'Reset view' }).click();
    const reset = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(reset.camera.semanticDistance).toBe(expected);
  });

  describe('System Map body names', () => {
    const labelNodes = (container: HTMLElement) =>
      [
        ...container.querySelectorAll<HTMLElement>(
          '[data-system-label-body-id]',
        ),
      ].map((node) => ({
        id: node.dataset.systemLabelBodyId,
        text: node.textContent,
        node,
      }));

    it('names the stars and planets without a hover, in an inert overlay', async () => {
      const view = render(SpatialCanvas, {
        scene: buildSystemScene(solLikeSystemDetail(), 1),
      });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      const layer = view.container.querySelector('.system-label-layer');
      expect(layer).not.toBeNull();
      expect(layer).toHaveAttribute('aria-hidden', 'true');
      const labels = labelNodes(view.container);
      expect(layer).toHaveAttribute(
        'data-system-label-count',
        String(labels.length),
      );
      const names = labels.map((label) => label.text);
      // This canvas is small (640 by 360), so crowded inner planets may lose
      // their names; the star and the well-separated outer planets keep them.
      expect(names).toEqual(
        expect.arrayContaining(['Sol-like', 'Jupiter', 'Saturn', 'Neptune']),
      );
      expect(names.length).toBeGreaterThanOrEqual(5);
      // Names must not be a second set of controls or announcements.
      expect(layer!.querySelectorAll('button, a, [tabindex]')).toHaveLength(0);
      expect(
        screen.queryByRole('button', { name: /Earth/ }),
      ).not.toBeInTheDocument();
      expect(view.container.querySelector('[data-hovered-body-id]')).toBeNull();
    });

    it('marks the selected body and keeps names with their bodies as the camera moves', async () => {
      const scene = buildSystemScene(solLikeSystemDetail(), 1);
      const earth = scene.bodies.find((body) => body.name === 'Earth')!;
      const view = render(SpatialCanvas, {
        scene: {
          ...scene,
          camera: { ...scene.camera, focus: { kind: 'body', ref: earth.ref } },
        },
      });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      const selected = view.container.querySelectorAll(
        '.system-label.is-selected',
      );
      expect(selected).toHaveLength(1);
      expect(selected[0]).toHaveTextContent('Earth');
      expect(selected[0]).toHaveAttribute(
        'data-system-label-body-id',
        String(earth.ref.bodyId),
      );
      const before = labelNodes(view.container).find(
        (label) => label.text === 'Jupiter',
      )!;
      const placed = (node: HTMLElement) => node.getAttribute('style');
      const beforeStyle = placed(before.node);
      screen.getByRole('button', { name: 'Zoom in' }).click();
      await waitFor(() => {
        const after = labelNodes(view.container).find(
          (label) => label.text === 'Jupiter',
        );
        // Zooming toward Earth moves Jupiter's label with it, or drops it.
        expect(after === undefined || placed(after.node) !== beforeStyle).toBe(
          true,
        );
      });
    });

    it('draws no body names on a galaxy map', async () => {
      const view = render(SpatialCanvas, {
        props: { scene: buildGalaxyReviewFixtureScene() },
      });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      expect(view.container.querySelector('.system-label-layer')).toBeNull();
    });

    it('draws no body names before the canvas has a size', async () => {
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(0);
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(0);
      const view = render(SpatialCanvas, {
        scene: buildSystemScene(solLikeSystemDetail(), 1),
      });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      expect(view.container.querySelector('.system-label-layer')).toBeNull();
    });
  });

  describe('a system map that loads before its canvas has a size', () => {
    const hiddenScene: SpatialSceneContract = {
      kind: 'system',
      revision: 4,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 153,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 4,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    const setCameras = (runtime: (typeof adapter.runtimes)[number]) =>
      runtime.dispatch.mock.calls
        .map(([command]) => command)
        .filter((command) => command.type === 'SET_CAMERA');
    const showAt = (width: number, height: number): void => {
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(
        width,
      );
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(
        height,
      );
      observerCallback(
        [{ contentRect: { width, height } } as unknown as ResizeObserverEntry],
        {} as ResizeObserver,
      );
    };

    it('waits for a real size, then fits and applies the overview', async () => {
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(0);
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(0);
      render(SpatialCanvas, { props: { scene: hiddenScene } });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      const runtime = adapter.runtimes[0]!;
      await waitFor(() =>
        expect(
          runtime.dispatch.mock.calls.some(
            ([command]) => command.type === 'LOAD_SCENE',
          ),
        ).toBe(true),
      );
      // Fitting a 1 × 1 stand-in would widen the overview for nothing.
      expect(setCameras(runtime)).toHaveLength(0);

      showAt(600, 600);
      const expected = Math.ceil(153 * SYSTEM_OVERVIEW_REFERENCE_ASPECT);
      const applied = setCameras(runtime);
      expect(applied).toHaveLength(1);
      expect(applied[0].camera.semanticDistance).toBe(expected);
      // The camera command is what draws the first frame at the real size, so
      // it must carry a newer revision than the contract's.
      expect(applied[0].camera.revision).toBeGreaterThan(4);

      screen.getByRole('button', { name: 'Zoom in' }).click();
      screen.getByRole('button', { name: 'Reset view' }).click();
      expect(setCameras(runtime).at(-1).camera.semanticDistance).toBe(expected);

      // Later resizes of a settled canvas do not move the camera again.
      const before = setCameras(runtime).length;
      showAt(640, 360);
      expect(setCameras(runtime)).toHaveLength(before);
    });

    it('keeps a camera the commander moved while the canvas was hidden', async () => {
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(0);
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(0);
      render(SpatialCanvas, { props: { scene: hiddenScene } });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      const runtime = adapter.runtimes[0]!;
      await waitFor(() =>
        expect(
          runtime.dispatch.mock.calls.some(
            ([command]) => command.type === 'LOAD_SCENE',
          ),
        ).toBe(true),
      );
      screen.getByRole('button', { name: 'Zoom in' }).click();
      const zoomed = setCameras(runtime).at(-1);
      expect(zoomed.camera.semanticDistance).toBeLessThan(153);

      showAt(600, 600);
      // No fit command overrides the zoom ...
      expect(setCameras(runtime).at(-1)).toBe(zoomed);
      // ... but Reset view still returns to the fitted overview.
      screen.getByRole('button', { name: 'Reset view' }).click();
      expect(setCameras(runtime).at(-1).camera.semanticDistance).toBe(
        Math.ceil(153 * SYSTEM_OVERVIEW_REFERENCE_ASPECT),
      );
    });
  });

  it('does not re-fit an already-fitted camera on F4 context recovery', async () => {
    feature.finder = true;
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(684);
    const scene: SpatialSceneContract = {
      kind: 'system',
      revision: 5,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 38,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 5,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    render(SpatialCanvas, { props: { scene } });
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const runtime = adapter.runtimes[0]!;
    // 38 at the reference aspect widens to 76 on a 600 × 684 canvas.
    const expected = Math.ceil(
      38 * (SYSTEM_OVERVIEW_REFERENCE_ASPECT / (600 / 684)),
    );
    await waitFor(() => {
      const fitted = runtime.dispatch.mock.calls.findLast(
        ([command]) => command.type === 'SET_CAMERA',
      )?.[0];
      expect(fitted?.camera.semanticDistance).toBe(expected);
    });
    const loadsBefore = runtime.dispatch.mock.calls.filter(
      ([command]) => command.type === 'LOAD_SCENE',
    ).length;
    runtime.emit({ type: 'RECOVERED', detail: 'Test context restored' });
    await waitFor(() =>
      expect(
        runtime.dispatch.mock.calls.filter(
          ([command]) => command.type === 'LOAD_SCENE',
        ),
      ).toHaveLength(loadsBefore + 1),
    );
    const recovered = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'LOAD_SCENE',
    )?.[0];
    // The recovery resumes the fitted camera without compounding the fit.
    expect(recovered.scene.camera.semanticDistance).toBe(expected);
    const lastSet = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(lastSet.camera.semanticDistance).toBe(expected);
    screen.getByRole('button', { name: 'Reset view' }).click();
    const reset = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(reset.camera.semanticDistance).toBe(expected);
  });

  it('lets a large system overview zoom back out past the default ceiling', async () => {
    const scene: SpatialSceneContract = {
      kind: 'system',
      revision: 3,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 153,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 3,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    render(SpatialCanvas, { props: { scene } });
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const runtime = adapter.runtimes[0]!;
    screen.getByRole('button', { name: 'Zoom out' }).click();
    const zoomed = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    // The old fixed 120 ceiling pulled the camera inward on zoom out.
    expect(zoomed.camera.semanticDistance).toBeGreaterThan(153);
    expect(zoomed.camera.semanticDistance).toBeLessThanOrEqual(153 * 1.5);
  });

  it('provides orbit, zoom and click controls for a system scene', async () => {
    const scene: SpatialSceneContract = {
      kind: 'system',
      revision: 12,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 38,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 12,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(
        screen.getByRole('application', { name: /Interactive 3D system map/ }),
      ).toBeVisible(),
    );
    await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
    const host = view.container.querySelector('.spatial-canvas')!;
    const runtime = adapter.runtimes[0]!;
    host.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
    );
    const rotated = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(rotated.camera).toMatchObject({
      systemId64: '42',
      bearingRad: scene.camera.bearingRad + 0.12,
    });
    screen.getByRole('button', { name: 'Zoom in' }).click();
    const zoomed = runtime.dispatch.mock.calls.findLast(
      ([command]) => command.type === 'SET_CAMERA',
    )?.[0];
    expect(zoomed.camera.semanticDistance).toBeLessThan(
      scene.camera.semanticDistance,
    );
    runtime.dispatch.mockClear();
    host.dispatchEvent(
      new PointerEvent('pointerdown', {
        pointerId: 5,
        button: 0,
        clientX: 30,
        clientY: 40,
        bubbles: true,
      }),
    );
    host.dispatchEvent(
      new PointerEvent('pointerup', {
        pointerId: 5,
        button: 0,
        clientX: 30,
        clientY: 40,
        bubbles: true,
      }),
    );
    expect(runtime.dispatch).toHaveBeenCalledWith({
      type: 'PICK',
      screenX: 30,
      screenY: 40,
    });
  });

  it('shows only the planet families present in the current system, including unknown classes', async () => {
    const detail = systemDetailFromV3(
      v3System(),
      [
        v3Body(),
        ...[
          'Earth-like world',
          'Earth-like world',
          'Class I gas giant',
          'Icy body',
          null,
          'Unrecognised catalogue class',
        ].map((bodyClass, index) =>
          v3Body({
            body_id: String(index + 1),
            frontier_body_id: String(index + 1),
            body_type: 'planet',
            body_class: bodyClass,
            body_class_source: bodyClass == null ? null : 'ratings-v4-derived',
          }),
        ),
      ],
      [],
      [],
    );
    const view = render(SpatialCanvas, { scene: buildSystemScene(detail, 1) });
    const legend = screen.getByRole('list', {
      name: 'Planet class colour legend',
    });
    expect(
      within(legend)
        .getAllByRole('listitem')
        .map((item) => item.textContent?.trim()),
    ).toEqual(['Earth-like', 'Gas giant · I', 'Icy', 'Unknown class']);
    expect(
      legend.querySelector('[data-body-family="unknown"] .swatch'),
    ).toHaveClass('unknown');
    expect(within(legend).queryByText('Water world')).not.toBeInTheDocument();

    await view.rerender({
      scene: buildSystemScene(
        {
          ...detail,
          bodies: detail.bodies.filter((body) => body.bodyClass === 'Icy body'),
        },
        2,
      ),
    });
    expect(
      within(legend)
        .getAllByRole('listitem')
        .map((item) => item.textContent?.trim()),
    ).toEqual(['Icy']);
    await view.rerender({
      scene: buildSystemScene({ ...detail, bodies: [detail.bodies[0]!] }, 3),
    });
    expect(
      screen.queryByRole('list', { name: 'Planet class colour legend' }),
    ).toBeNull();
  });

  it.each(['Earth-like world', 'Unrecognised catalogue class', null])(
    'shows raw planet class %s on hover without carrying facts across systems',
    async (bodyClass) => {
      const scene = buildSystemScene(
        systemDetailFromV3(
          v3System({ id64: '42' }),
          [
            v3Body({
              name: 'Hovered planet',
              body_type: 'planet',
              body_class: bodyClass,
              body_class_source:
                bodyClass == null ? null : 'ratings-v4-derived',
            }),
          ],
          [],
          [],
        ),
        1,
      );
      const view = render(SpatialCanvas, { scene });
      await waitFor(() => expect(adapter.runtimes).toHaveLength(1));
      const runtime = adapter.runtimes[0]!;
      runtime.emit({
        type: 'TARGET_HOVERED',
        target: { kind: 'body', ref: { systemId64: '42', bodyId: 0 } },
      });
      // The always-visible label also carries this name, so look at the hover
      // card itself rather than at any text match.
      const hoverCard = () =>
        view.container.querySelector<HTMLElement>('[data-hovered-body-id]');
      const hover = await waitFor(() => {
        const card = hoverCard();
        expect(card).not.toBeNull();
        return card!;
      });
      expect(within(hover).getByText('Hovered planet')).toBeVisible();
      expect(within(hover).getByText(bodyClass ?? 'Planet')).toBeVisible();
      const source = within(hover).queryByText(
        'Source: Ratings V4 derived catalogue',
      );
      if (bodyClass == null) expect(source).toBeNull();
      else expect(source).toBeVisible();
      runtime.emit({ type: 'TARGET_HOVERED' });
      await waitFor(() => expect(hoverCard()).toBeNull());

      runtime.emit({
        type: 'TARGET_HOVERED',
        target: { kind: 'body', ref: { systemId64: '99', bodyId: 0 } },
      });
      expect(hoverCard()).toBeNull();
      runtime.emit({
        type: 'TARGET_HOVERED',
        target: { kind: 'body', ref: { systemId64: '42', bodyId: 0 } },
      });
      await waitFor(() => expect(hoverCard()).not.toBeNull());
      await view.rerender({
        scene: { ...scene, revision: 2, systemId64: '99', bodies: [] },
      });
      expect(hoverCard()).toBeNull();
      await view.rerender({ scene: { ...scene, revision: 3 } });
      expect(hoverCard()).toBeNull();
    },
  );

  it('projects named Finder buttons with selected emphasis and preserved identities', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(
        view.container.querySelector('[data-visible-label-count]'),
      ).toHaveAttribute('data-visible-label-count', '3'),
    );
    const systemLabels = view.container.querySelectorAll(
      '[data-map-label-kind="system"]',
    );
    expect(systemLabels).toHaveLength(3);
    expect(
      view.container.querySelector('[data-map-label-selected="true"]'),
    ).toHaveTextContent('Fixture Core');
    for (const label of systemLabels) {
      expect(label.tagName).toBe('BUTTON');
      expect(label).toHaveAttribute('data-system-id64');
      expect(label.closest('[aria-hidden="true"]')).toBeNull();
    }
  });

  it('keeps Nearby systems collapsed and exposes overlapping candidates for keyboard selection and focus', async () => {
    const onRuntimeEvent = vi.fn();
    const view = render(SpatialCanvas, {
      scene: nearbyScene(),
      onRuntimeEvent,
    });
    const summary = await screen.findByText('Nearby systems (3)');
    const disclosure = summary.closest('details')!;
    expect(disclosure).not.toHaveAttribute('open');
    summary.focus();
    expect(summary).toHaveFocus();
    // Native summary/button keyboard activation dispatches a detail: 0 click.
    await fireEvent.click(summary, { detail: 0 });
    expect(disclosure).toHaveAttribute('open');
    const list = await screen.findByRole('list', { name: 'Nearby systems' });
    expect(
      within(list)
        .getAllByRole('button')
        .map((button) => button.dataset.systemId64),
    ).toEqual([
      catalogueStar.systemId64,
      finderStar.systemId64,
      overlappingStar.systemId64,
    ]);
    expect(
      view.container.querySelector(
        `[data-map-label-key="system:${overlappingStar.systemId64}"]`,
      ),
    ).toBeNull();

    const button = within(list).getByRole('button', {
      name: overlappingStar.name,
    });
    button.focus();
    await tick();
    expect(button).toHaveFocus();
    const tooltip = screen.getByRole('tooltip', {
      name: `System ${overlappingStar.name}`,
    });
    expect(button.getAttribute('aria-describedby')?.split(/\s+/u)).toContain(
      tooltip.id,
    );
    expect(tooltip).toHaveAttribute(
      'data-system-id64',
      overlappingStar.systemId64,
    );
    expect(onRuntimeEvent).toHaveBeenCalledWith({
      type: 'TARGET_HOVERED',
      target: {
        kind: 'system',
        systemId64: overlappingStar.systemId64,
        name: overlappingStar.name,
      },
    });
    expect(onRuntimeEvent).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'TARGET_PICKED' }),
    );
    await fireEvent.keyDown(button, { key: 'Enter' });
    await fireEvent.click(button, { detail: 0 });
    const target = { kind: 'system', systemId64: overlappingStar.systemId64 };
    expect(onRuntimeEvent).toHaveBeenCalledWith({
      type: 'TARGET_PICKED',
      target,
    });
    expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
      type: 'FLY_TO',
      target,
      reducedMotion: true,
    });
    expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
      'tabindex',
      '0',
    );
  });

  it('keeps list focus and order through hover ranking and returns from the card on Escape', async () => {
    render(SpatialCanvas, { scene: nearbyScene() });
    await fireEvent.click(await screen.findByText('Nearby systems (3)'));
    const list = await screen.findByRole('list', { name: 'Nearby systems' });
    const originalOrder = within(list).getAllByRole('button');
    const opener = within(list).getByRole('button', {
      name: overlappingStar.name,
    });
    expect(originalOrder.at(-1)).toBe(opener);
    vi.useFakeTimers();
    opener.focus();
    await tick();
    await vi.advanceTimersByTimeAsync(250);
    await tick();
    expect(opener).toHaveFocus();
    expect(within(list).getAllByRole('button')).toEqual(originalOrder);

    await fireEvent.keyDown(opener, { key: 'ArrowRight' });
    await tick();
    const tooltip = screen.getByRole('tooltip', {
      name: `System ${overlappingStar.name}`,
    });
    const select = within(tooltip).getByRole('button', { name: 'Select' });
    expect(select).toHaveFocus();
    // Native Tab navigation remains available inside and outside the card.
    expect(await fireEvent.keyDown(select, { key: 'Tab' })).toBe(true);
    await fireEvent.keyDown(select, { key: 'Escape' });
    await tick();
    expect(opener).toHaveFocus();
    expect(screen.queryByRole('tooltip')).toBeNull();
    await vi.advanceTimersByTimeAsync(250);
    await tick();
    expect(opener).toHaveFocus();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(within(list).getAllByRole('button')).toEqual(originalOrder);
  });

  it('preserves the newer region hover when the preceding system card is dismissed', async () => {
    const onRuntimeEvent = vi.fn();
    const view = render(SpatialCanvas, {
      scene: nearbyScene(),
      onRuntimeEvent,
    });
    await screen.findByText('Nearby systems (3)');
    const runtime = adapter.runtimes[0]!;
    runtime.emit({
      type: 'TARGET_HOVERED',
      target: {
        kind: 'system',
        systemId64: catalogueStar.systemId64,
        name: catalogueStar.name,
      },
      screenX: 100,
      screenY: 80,
    });
    await screen.findByRole('tooltip', {
      name: `System ${catalogueStar.name}`,
    });
    vi.useFakeTimers();
    const regionHover = {
      type: 'TARGET_HOVERED',
      target: { kind: 'region', id: '18' },
    } as const;
    onRuntimeEvent.mockClear();
    runtime.emit(regionHover);
    await tick();
    await vi.advanceTimersByTimeAsync(220);
    await tick();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(onRuntimeEvent).toHaveBeenLastCalledWith(regionHover);
    expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
      'data-last-hovered-region-id',
      '18',
    );
  });

  it('preserves catalogue identity on label hover and click without triggering a canvas pick', async () => {
    const onSystemSelect = vi.fn();
    const onRuntimeEvent = vi.fn();
    const view = render(SpatialCanvas, {
      scene: nearbyScene(),
      onSystemSelect,
      onRuntimeEvent,
    });
    const label = await screen.findByRole('button', {
      name: `Select ${catalogueStar.name}`,
    });
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    await fireEvent.pointerEnter(label, {
      clientX: 100,
      clientY: 80,
      pointerType: 'mouse',
    });
    const tooltip = screen.getByRole('tooltip', {
      name: `System ${catalogueStar.name}`,
    });
    expect(label).toHaveAttribute('data-system-id64', catalogueStar.systemId64);
    expect(label.getAttribute('aria-describedby')?.split(/\s+/u)).toContain(
      tooltip.id,
    );
    expect(onRuntimeEvent).toHaveBeenCalledWith({
      type: 'TARGET_HOVERED',
      target: {
        kind: 'system',
        systemId64: catalogueStar.systemId64,
        name: catalogueStar.name,
      },
    });
    expect(within(tooltip).getByText('G2', { exact: true })).toBeVisible();
    expect(within(tooltip).queryByText('G class star')).toBeNull();
    await fireEvent.pointerDown(label, { button: 0, pointerId: 1 });
    await fireEvent.pointerUp(label, { button: 0, pointerId: 1 });
    await fireEvent.click(label);
    expect(onSystemSelect).toHaveBeenCalledExactlyOnceWith(catalogueStar);
    expect(
      runtime.dispatch.mock.calls.some(([command]) => command.type === 'PICK'),
    ).toBe(false);
    expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
      'data-last-hovered-system-id',
      catalogueStar.systemId64,
    );
  });

  it('shows renderer hover cards near the pointer and passes axe with the expanded list', async () => {
    const view = render(SpatialCanvas, {
      scene: nearbyScene(),
      anchorPoint: finderStar,
    });
    const summary = await screen.findByText('Nearby systems (3)');
    await fireEvent.click(summary);
    expect(
      await screen.findByRole('list', { name: 'Nearby systems' }),
    ).toBeVisible();
    adapter.runtimes[0]!.emit({
      type: 'TARGET_HOVERED',
      target: {
        kind: 'system',
        systemId64: catalogueStar.systemId64,
        name: catalogueStar.name,
      },
      screenX: 100,
      screenY: 80,
    });
    const tooltip = await screen.findByRole('tooltip', {
      name: `System ${catalogueStar.name}`,
    });
    expect(tooltip.parentElement).toHaveStyle({ left: '114px', top: '94px' });
    expect(within(tooltip).getByText('G2', { exact: true })).toBeVisible();
    expect(
      within(tooltip).getByText('Distance from Finder Reach'),
    ).toBeVisible();
    expect(within(tooltip).getByText('180.0 LY')).toBeVisible();
    expect(
      within(tooltip).getByRole('link', { name: 'Inspect' }),
    ).toHaveAttribute('href', `/inspect?system=${catalogueStar.systemId64}`);
    const result = await axe.run(view.container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it('ranks candidates once the camera settles but keeps every name on its star while it moves', async () => {
    const candidates = vi.spyOn(galaxyLabels, 'galaxyLabelCandidates');
    const layout = vi.spyOn(galaxyLabels, 'layoutGalaxyLabels');
    const scene = nearbyScene();
    const view = render(SpatialCanvas, { scene });
    await screen.findByText('Nearby systems (3)');
    const layer = view.container.querySelector('.galaxy-label-layer')!;
    candidates.mockClear();
    layout.mockClear();
    vi.useFakeTimers();
    const runtime = adapter.runtimes[0]!;
    for (let revision = 2; revision <= 4; revision += 1) {
      layout.mockClear();
      runtime.emit({
        type: 'CAMERA_CHANGED',
        camera: { ...scene.camera, bearingRad: revision / 10, revision },
      });
      await tick();
      // Names are re-placed from the live camera on every change and never
      // hidden: they must not vanish and reappear once the camera stops.
      expect(layout).toHaveBeenCalledOnce();
      expect(layout.mock.calls[0]?.[1].revision).toBe(revision);
      // Region names never jump aside: the layout only remembers which were
      // faded, and gets the lookup that keeps each name inside its region.
      expect(layout.mock.calls[0]?.[3]).toHaveProperty('previouslyMuted');
      expect(layout.mock.calls[0]?.[3]).not.toHaveProperty('lockDisplacements');
      expect(layer).not.toHaveClass('labels-moving');
      await vi.advanceTimersByTimeAsync(100);
      // Choosing which names exist scans the catalogue, so it waits.
      expect(candidates).not.toHaveBeenCalled();
    }
    await vi.advanceTimersByTimeAsync(79);
    expect(candidates).not.toHaveBeenCalled();
    layout.mockClear();
    await vi.advanceTimersByTimeAsync(1);
    await tick();
    expect(candidates).toHaveBeenCalledOnce();
    expect(candidates.mock.calls[0]?.[0].camera.revision).toBe(4);
    // The new names are placed against the same camera, once.
    expect(layout).toHaveBeenCalledOnce();
    expect(layout.mock.calls[0]?.[0]).toHaveLength(3);
    expect(layout.mock.calls[0]?.[1].revision).toBe(4);
    expect(layout.mock.calls[0]?.[3]).not.toHaveProperty('lockDisplacements');
    await vi.advanceTimersByTimeAsync(1_000);
    expect(candidates).toHaveBeenCalledOnce();
    expect(layout).toHaveBeenCalledOnce();
  });

  it('puts names on their points with no easing and eases only the fade of a crowded name', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/lib/spatial/SpatialCanvas.svelte'),
      'utf8',
    );
    const rule = (selector: string) => {
      const match = new RegExp(`\\n  ${selector} \\{([^}]*)\\}`, 'u').exec(
        source,
      );
      expect(match, selector).not.toBeNull();
      return match![1]!;
    };
    // A name that trails its star during a drag reads as the wrong star, so
    // left/top follow the camera exactly...
    expect(rule('\\.galaxy-label')).not.toMatch(/transition|will-change/u);
    // ...and a region name never slides aside: only its fade is eased.
    const region = rule('\\.galaxy-label--region');
    expect(region).not.toMatch(/--label-dx|--label-dy|(?<![-\w])transform:/u);
    expect(region).toMatch(/transition: opacity/u);
    expect(rule('\\.galaxy-label--region\\.is-muted')).toMatch(/opacity/u);
    expect(source).toMatch(/style:left=\{`\$\{label\.xPx\}px`\}/u);
  });

  it('distinguishes a click from a pan and cancels interrupted gestures without picking', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const host = view.container.querySelector('.spatial-canvas')!;
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    const pointer = (type: string, x: number) =>
      host.dispatchEvent(
        new PointerEvent(type, {
          pointerId: 1,
          button: 0,
          clientX: x,
          clientY: 100,
          bubbles: true,
        }),
      );
    pointer('pointerdown', 100);
    pointer('pointermove', 180);
    pointer('pointerup', 180);
    expect(
      runtime.dispatch.mock.calls.some(
        ([command]) => command.type === 'SET_CAMERA',
      ),
    ).toBe(true);
    expect(
      runtime.dispatch.mock.calls.some(([command]) => command.type === 'PICK'),
    ).toBe(false);
    pointer('pointerdown', 180);
    pointer('pointercancel', 180);
    pointer('pointerup', 180);
    expect(
      runtime.dispatch.mock.calls.some(([command]) => command.type === 'PICK'),
    ).toBe(false);
    pointer('pointerdown', 200);
    pointer('pointerup', 200);
    expect(runtime.dispatch).toHaveBeenCalledWith({
      type: 'PICK',
      screenX: 200,
      screenY: 100,
    });
  });

  it.each([
    { name: 'one-finger pan', options: {}, mode: 'pan' },
    {
      name: 'default mouse pan',
      options: { pointerType: 'mouse' },
      mode: 'pan',
    },
    {
      name: 'Shift-drag orbit',
      options: { pointerType: 'mouse', shiftKey: true },
      mode: 'orbit',
    },
    {
      name: 'right-button orbit',
      options: { pointerType: 'mouse', button: 2 },
      mode: 'orbit',
    },
    {
      name: 'Alt-drag elevation',
      options: { pointerType: 'mouse', altKey: true },
      mode: 'elevate',
    },
    {
      name: 'Shift taking precedence over Alt',
      options: { pointerType: 'mouse', altKey: true, shiftKey: true },
      mode: 'orbit',
    },
  ])(
    'supports $name without selecting after a drag',
    async ({ options, mode }) => {
      const scene = buildGalaxyReviewFixtureScene();
      const view = render(SpatialCanvas, { props: { scene } });
      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveAttribute(
          'data-renderer-state',
          'ready',
        ),
      );
      const host = view.container.querySelector('.spatial-canvas')!;
      const runtime = adapter.runtimes[0]!;
      runtime.dispatch.mockClear();
      pointer(host, 'pointerdown', 1, 320, 180, options);
      pointer(window, 'pointermove', 1, 322, 180, options);
      expect(
        runtime.dispatch.mock.calls.filter(
          ([command]) => command.type === 'SET_CAMERA',
        ),
      ).toHaveLength(0);
      pointer(window, 'pointermove', 1, 360, 160, options);
      const expected =
        mode === 'orbit'
          ? orbitGalaxyCamera(scene.camera, -40 * 0.006, -20 * 0.004)
          : mode === 'elevate'
            ? elevateGalaxyCameraByPixels(scene.camera, 20, {
                width: 640,
                height: 360,
              })
            : panGalaxyCamera(scene.camera, -40, 20, {
                width: 640,
                height: 360,
              });
      expect(lastGalaxyCamera()).toEqual(expected);
      pointer(window, 'pointerup', 1, 360, 160, options);
      expect(
        runtime.dispatch.mock.calls.some(
          ([command]) => command.type === 'PICK',
        ),
      ).toBe(false);
    },
  );

  it('pinches and pans around the two-finger midpoint, ignores a third touch and resumes one-finger pan', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: { ...fixture.camera, distanceLy: 1_000 },
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const host = view.container.querySelector('.spatial-canvas')!;
    const canvas = view.container.querySelector('canvas')!;
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 50,
      width: 640,
      height: 360,
    } as DOMRect);
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    pointer(host, 'pointerdown', 1, 340, 230);
    pointer(host, 'pointerdown', 2, 500, 230);
    pointer(host, 'pointerdown', 3, 600, 300);
    pointer(window, 'pointermove', 3, 610, 310);
    pointer(window, 'pointercancel', 3, 610, 310);
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'SET_CAMERA',
      ),
    ).toHaveLength(0);
    pointer(window, 'pointermove', 1, 300, 250);
    pointer(window, 'pointermove', 2, 580, 250);
    const pinched = lastGalaxyCamera();
    expect(pinched.distanceLy).toBeCloseTo((1_000 * 160) / 280, 8);
    const anchored = projectGalaxyLabel(scene.camera.focusLy, pinched, {
      width: 640,
      height: 360,
    });
    expect(anchored?.xPx).toBeCloseTo(340, 6);
    expect(anchored?.yPx).toBeCloseTo(200, 6);
    expect(pinched.focusLy.y).toBe(scene.camera.focusLy.y);

    // Moving both fingers together pans while restoring the same final range.
    pointer(window, 'pointermove', 1, 330, 270);
    pointer(window, 'pointermove', 2, 610, 270);
    const translated = lastGalaxyCamera();
    expect(translated.distanceLy).toBeCloseTo(pinched.distanceLy, 8);
    const translatedAnchor = projectGalaxyLabel(
      scene.camera.focusLy,
      translated,
      { width: 640, height: 360 },
    );
    expect(translatedAnchor?.xPx).toBeCloseTo(370, 6);
    expect(translatedAnchor?.yPx).toBeCloseTo(220, 6);

    pointer(window, 'pointerup', 2, 610, 270);
    pointer(window, 'pointermove', 1, 350, 270);
    expect(lastGalaxyCamera()).toEqual(
      panGalaxyCamera(translated, -20, 0, { width: 640, height: 360 }),
    );
    pointer(window, 'pointerup', 1, 350, 270);
    expect(
      runtime.dispatch.mock.calls.some(([command]) => command.type === 'PICK'),
    ).toBe(false);
  });

  it('keeps window gesture handling when synthetic pointers cannot be captured', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const host = view.container.querySelector('.spatial-canvas')!;
    Object.defineProperty(host, 'setPointerCapture', {
      value: () => {
        throw new DOMException('Pointer is not active', 'NotFoundError');
      },
    });
    pointer(host, 'pointerdown', 1, 320, 180);
    pointer(window, 'pointermove', 1, 360, 180);
    expect(lastGalaxyCamera().focusLy.x).toBeLessThan(scene.camera.focusLy.x);
    pointer(window, 'pointercancel', 1, 360, 180);
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    pointer(window, 'pointermove', 1, 400, 180);
    pointer(window, 'pointerup', 1, 400, 180);
    expect(runtime.dispatch).not.toHaveBeenCalled();
  });

  it.each(['pointercancel', 'lostpointercapture'])(
    'releases both fingers on %s and accepts a fresh gesture',
    async (eventType) => {
      const scene = buildGalaxyReviewFixtureScene();
      const view = render(SpatialCanvas, { props: { scene } });
      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveAttribute(
          'data-renderer-state',
          'ready',
        ),
      );
      const host = view.container.querySelector('.spatial-canvas')!;
      const runtime = adapter.runtimes[0]!;
      const captured = new Set<number>();
      const release = vi.fn((id: number) => captured.delete(id));
      Object.defineProperties(host, {
        setPointerCapture: { value: (id: number) => captured.add(id) },
        hasPointerCapture: { value: (id: number) => captured.has(id) },
        releasePointerCapture: { value: release },
      });
      const removeListener = vi.spyOn(window, 'removeEventListener');
      pointer(host, 'pointerdown', 1, 220, 180);
      pointer(host, 'pointerdown', 2, 420, 180);
      pointer(host, eventType, 1, 220, 180);
      expect(release.mock.calls).toEqual([[1], [2]]);
      expect(removeListener).toHaveBeenCalledWith(
        'pointermove',
        expect.any(Function),
      );
      expect(removeListener).toHaveBeenCalledWith(
        'pointerup',
        expect.any(Function),
      );
      expect(removeListener).toHaveBeenCalledWith(
        'pointercancel',
        expect.any(Function),
      );
      runtime.dispatch.mockClear();
      pointer(window, 'pointermove', 2, 440, 180);
      pointer(window, 'pointerup', 1, 220, 180);
      pointer(window, 'pointerup', 2, 440, 180);
      expect(runtime.dispatch).not.toHaveBeenCalled();
      pointer(host, 'pointerdown', 4, 320, 180);
      pointer(window, 'pointermove', 4, 360, 180);
      expect(lastGalaxyCamera().focusLy.x).toBeLessThan(scene.camera.focusLy.x);
      runtime.dispatch.mockClear();
      view.unmount();
      expect(release).toHaveBeenLastCalledWith(4);
      runtime.dispatch.mockClear();
      pointer(window, 'pointermove', 4, 400, 180);
      pointer(window, 'pointerup', 4, 400, 180);
      expect(runtime.dispatch).not.toHaveBeenCalled();
    },
  );

  it('wheel-zooms at canvas-relative cursor coordinates while the buttons zoom about the centre', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: { ...fixture.camera, distanceLy: 1_000 },
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const host = view.container.querySelector('.spatial-canvas')!;
    const canvas = view.container.querySelector('canvas')!;
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 50,
      width: 640,
      height: 360,
    } as DOMRect);
    const point = { x: 200, y: scene.camera.focusLy.y, z: 180 };
    const before = projectGalaxyLabel(point, scene.camera, {
      width: 640,
      height: 360,
    })!;
    const wheel = new WheelEvent('wheel', {
      deltaY: -240,
      clientX: before.xPx + 100,
      clientY: before.yPx + 50,
      bubbles: true,
      cancelable: true,
    });
    host.dispatchEvent(wheel);
    expect(wheel.defaultPrevented).toBe(true);
    const zoomed = lastGalaxyCamera();
    expect(zoomed.distanceLy).toBeLessThan(scene.camera.distanceLy);
    const after = projectGalaxyLabel(point, zoomed, {
      width: 640,
      height: 360,
    })!;
    expect(after.xPx).toBeCloseTo(before.xPx, 6);
    expect(after.yPx).toBeCloseTo(before.yPx, 6);
    screen.getByRole('button', { name: 'Zoom in' }).click();
    expect(lastGalaxyCamera().focusLy).toEqual(zoomed.focusLy);
    expect(lastGalaxyCamera().distanceLy).toBeLessThan(zoomed.distanceLy);
    screen.getByRole('button', { name: 'Zoom out' }).click();
    expect(lastGalaxyCamera().focusLy).toEqual(zoomed.focusLy);
  });

  it('floats the controls over the map and still zooms when scrolled over them', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: { ...fixture.camera, distanceLy: 1_000 },
    };
    const view = render(SpatialCanvas, {
      props: { scene, controlsOverlay: true },
    });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const surface = view.container.querySelector('.map-systems-surface')!;
    const bar = surface.querySelector('.map-overlay-bar')!;
    expect(bar).not.toBeNull();
    expect(
      bar.contains(
        screen.getByRole('group', { name: 'Galaxy camera controls' }),
      ),
    ).toBe(true);
    expect(bar.contains(screen.getByRole('status'))).toBe(true);
    const canvas = view.container.querySelector('canvas')!;
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 50,
      width: 640,
      height: 360,
    } as DOMRect);
    adapter.runtimes[0]!.dispatch.mockClear();
    const wheel = new WheelEvent('wheel', {
      deltaY: -240,
      clientX: 200,
      clientY: 390,
      bubbles: true,
      cancelable: true,
    });
    bar.querySelector('button')!.dispatchEvent(wheel);
    expect(wheel.defaultPrevented).toBe(true);
    expect(lastGalaxyCamera().distanceLy).toBeLessThan(1_000);
    // A pop-out opened from the bar (Sources) keeps its own scrolling.
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    const inside = document.createElement('p');
    dialog.append(inside);
    bar.append(dialog);
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    const dialogWheel = new WheelEvent('wheel', {
      deltaY: -240,
      clientX: 200,
      clientY: 300,
      bubbles: true,
      cancelable: true,
    });
    inside.dispatchEvent(dialogWheel);
    expect(dialogWheel.defaultPrevented).toBe(false);
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'SET_CAMERA',
      ),
    ).toHaveLength(0);
    dialog.remove();
  });

  it('keeps a zoomed-out galaxy centred when dragged or wheel-zoomed off centre', async () => {
    const fixture = buildGalaxyReviewFixtureScene();
    const scene = {
      ...fixture,
      camera: {
        ...fixture.camera,
        focusLy: GALAXY_NAVIGATION_CENTRE_LY,
        distanceLy: 150_000,
      },
    };
    const view = render(SpatialCanvas, { props: { scene } });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    const host = view.container.querySelector('.spatial-canvas')!;
    const canvas = view.container.querySelector('canvas')!;
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      top: 50,
      width: 640,
      height: 360,
    } as DOMRect);
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    // A long drag that would otherwise throw the galaxy off the screen.
    pointer(host, 'pointerdown', 1, 320, 180);
    pointer(window, 'pointermove', 1, 620, 340);
    pointer(window, 'pointerup', 1, 620, 340);
    expect(lastGalaxyCamera().focusLy).toEqual(GALAXY_NAVIGATION_CENTRE_LY);
    // Zooming in a little towards a corner may not pull it away either while
    // the whole galaxy still fits.
    host.dispatchEvent(
      new WheelEvent('wheel', {
        deltaY: -40,
        clientX: 120,
        clientY: 60,
        bubbles: true,
        cancelable: true,
      }),
    );
    const zoomed = lastGalaxyCamera();
    expect(zoomed.distanceLy).toBeLessThan(150_000);
    expect(zoomed.focusLy).toEqual(GALAXY_NAVIGATION_CENTRE_LY);
  });

  it('queues camera movements until an in-flight transition settles', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const scene = buildGalaxyReviewFixtureScene();
    const focusTarget = { kind: 'system' as const, systemId64: '42' };
    const view = render(SpatialCanvas, {
      props: { scene, focusTarget, focusRevision: 1 },
    });
    await waitFor(() =>
      expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
        type: 'LOAD_SCENE',
        scene,
      }),
    );
    await waitFor(() =>
      expect(adapter.runtimes[0]?.dispatch).toHaveBeenCalledWith({
        type: 'FLY_TO',
        target: focusTarget,
        reducedMotion: false,
      }),
    );

    const host = view.container.querySelector('.spatial-canvas');
    const runtime = adapter.runtimes[0];
    expect(host).toBeTruthy();
    expect(runtime).toBeTruthy();
    runtime?.dispatch.mockClear();

    host?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
    );
    expect(
      runtime?.dispatch.mock.calls.some(
        ([command]) => command.type === 'SET_CAMERA',
      ),
    ).toBe(false);

    runtime?.emit({ type: 'TRANSITION_FINISHED', target: focusTarget });
    await waitFor(() =>
      expect(
        runtime?.dispatch.mock.calls.some(
          ([command]) => command.type === 'SET_CAMERA',
        ),
      ).toBe(true),
    );
  });

  it.each(['overview', 'system focus'] as const)(
    'retains the settled %s camera when Finder data changes during the transition',
    async (movement) => {
      feature.finder = true;
      vi.stubGlobal('matchMedia', () => ({ matches: false }));
      const scene = buildGalaxyReviewFixtureScene();
      const focusTarget = {
        kind: 'system' as const,
        systemId64: '9007199254740994',
      };
      const settledCamera: CameraState = {
        ...scene.camera,
        focusLy: { x: 0, y: -6, z: 70 },
        distanceLy: movement === 'overview' ? 118_000 : 60,
        revision: 21,
      };
      const onTransitionChange = vi.fn();
      const view = render(SpatialCanvas, {
        props: {
          scene,
          onTransitionChange,
          ...(movement === 'overview'
            ? { cameraRequest: settledCamera, cameraRequestRevision: 1 }
            : { focusTarget, focusRevision: 1 }),
        },
      });
      await waitFor(() =>
        expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
          'data-camera-transition-active',
          'true',
        ),
      );
      const runtime = adapter.runtimes[0]!;
      runtime.dispatch.mockClear();
      // A real LOAD_SCENE rebuild emits its supplied camera synchronously.
      // Replaying the pre-flight scene camera here would undo the flight.
      runtime.dispatch.mockImplementation((command) => {
        if (command.type === 'LOAD_SCENE') {
          runtime.emit({
            type: 'CAMERA_CHANGED',
            camera: command.scene.camera,
          });
        }
        return { status: 'executed' };
      });
      const refreshedScene: SpatialSceneContract = {
        ...scene,
        revision: scene.revision + 1,
        contributions: [
          ...scene.contributions,
          {
            id: 'new-finder-result',
            owner: 'FINDER',
            revision: 1,
            layers: [
              {
                id: 'finder-systems',
                version: 1,
                representation: 'AUTHORITATIVE',
                targetCount: 1,
                truncated: false,
                payload: {
                  systems: [
                    {
                      systemId64: '10477373803000',
                      name: 'New Finder point',
                      positionLy: { x: 100, y: 30, z: 100 },
                    },
                  ],
                },
              },
            ],
          },
        ],
      };
      await view.rerender({ scene: refreshedScene });
      expect(runtime.dispatch).not.toHaveBeenCalled();

      runtime.emit({ type: 'CAMERA_CHANGED', camera: settledCamera });
      runtime.emit({
        type: 'TRANSITION_FINISHED',
        ...(movement === 'system focus' ? { target: focusTarget } : {}),
      });

      await waitFor(() =>
        expect(runtime.dispatch).toHaveBeenCalledWith({
          type: 'LOAD_SCENE',
          scene: { ...refreshedScene, camera: settledCamera },
        }),
      );
      expect(onTransitionChange).toHaveBeenLastCalledWith(false, settledCamera);
      expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
        'data-camera-revision',
        '21',
      );
      expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
        'data-system-target-count',
        '4',
      );
      await waitFor(() =>
        expect(
          view.container.querySelector(
            '[data-map-label-key="system:9007199254740994"]',
          ),
        ).toHaveTextContent('Fixture Core'),
      );
    },
  );

  it('keeps the destination camera when a queued scene enters a system', async () => {
    feature.finder = true;
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const scene = buildGalaxyReviewFixtureScene();
    const focusTarget = { kind: 'system' as const, systemId64: '42' };
    const view = render(SpatialCanvas, {
      props: { scene, focusTarget, focusRevision: 1 },
    });
    await waitFor(() =>
      expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
        'data-camera-transition-active',
        'true',
      ),
    );
    const runtime = adapter.runtimes[0]!;
    runtime.dispatch.mockClear();
    const destination: SpatialSceneContract = {
      kind: 'system',
      revision: 2,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: focusTarget,
        semanticDistance: 38,
        bearingRad: -0.85,
        pitchRad: 0.58,
        revision: 2,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };
    await view.rerender({ scene: destination });
    expect(runtime.dispatch).not.toHaveBeenCalled();
    runtime.emit({
      type: 'CAMERA_CHANGED',
      camera: { ...scene.camera, distanceLy: 60, revision: 3 },
    });
    runtime.emit({ type: 'TRANSITION_FINISHED', target: focusTarget });
    await waitFor(() =>
      expect(runtime.dispatch).toHaveBeenCalledWith({
        type: 'LOAD_SCENE',
        scene: destination,
      }),
    );
  });

  it('loads the unchanged queued camera from main when F4 is disabled', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const scene = buildGalaxyReviewFixtureScene();
    const focusTarget = { kind: 'system' as const, systemId64: '42' };
    const onTransitionChange = vi.fn();
    const view = render(SpatialCanvas, {
      props: { scene, focusTarget, focusRevision: 1, onTransitionChange },
    });
    await waitFor(() =>
      expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
        'data-camera-transition-active',
        'true',
      ),
    );
    const runtime = adapter.runtimes[0]!;
    const refreshedScene = { ...scene, revision: scene.revision + 1 };
    await view.rerender({ scene: refreshedScene });
    const settledCamera: CameraState = {
      ...scene.camera,
      focusLy: { x: 12, y: -6, z: 70 },
      distanceLy: 60,
      revision: 21,
    };
    runtime.emit({ type: 'CAMERA_CHANGED', camera: settledCamera });
    runtime.emit({ type: 'TRANSITION_FINISHED', target: focusTarget });

    await waitFor(() =>
      expect(runtime.dispatch).toHaveBeenLastCalledWith({
        type: 'LOAD_SCENE',
        scene: refreshedScene,
      }),
    );
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'LOAD_SCENE',
      ),
    ).toHaveLength(2);
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'FLY_TO',
      ),
    ).toHaveLength(1);
    expect(onTransitionChange).toHaveBeenCalledTimes(2);
    expect(onTransitionChange).toHaveBeenLastCalledWith(false, settledCamera);
  });

  it('keeps density patched during a flight in the queued and recovered F4 scene', async () => {
    feature.finder = true;
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const scene = buildGalaxyReviewFixtureScene();
    const focusTarget = { kind: 'system' as const, systemId64: '42' };
    const view = render(SpatialCanvas, {
      props: { scene, focusTarget, focusRevision: 1 },
    });
    await waitFor(() =>
      expect(view.container.querySelector('.spatial-canvas')).toHaveAttribute(
        'data-camera-transition-active',
        'true',
      ),
    );
    const runtime = adapter.runtimes[0]!;
    const contributionPatch = {
      ...scene.contributions[0]!,
      revision: scene.contributions[0]!.revision + 1,
    };
    // The parent snapshots its scene before the asynchronous density arrives.
    const queuedScene = { ...scene, revision: scene.revision + 1 };
    await view.rerender({ scene: queuedScene });
    await view.rerender({ contributionPatch, contributionPatchRevision: 1 });
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'LOAD_SCENE',
      ),
    ).toHaveLength(1);

    const settledCamera: CameraState = {
      ...scene.camera,
      distanceLy: 60,
      revision: 21,
    };
    runtime.emit({ type: 'CAMERA_CHANGED', camera: settledCamera });
    runtime.emit({ type: 'TRANSITION_FINISHED', target: focusTarget });

    const expectedContributions = [
      contributionPatch,
      ...queuedScene.contributions.slice(1),
    ];
    await waitFor(() =>
      expect(runtime.dispatch).toHaveBeenCalledWith({
        type: 'LOAD_SCENE',
        scene: {
          ...queuedScene,
          camera: settledCamera,
          contributions: expectedContributions,
        },
      }),
    );
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'PATCH_CONTRIBUTION',
      ),
    ).toHaveLength(1);

    runtime.emit({ type: 'RECOVERED', detail: 'Test context restored' });
    await waitFor(() =>
      expect(
        runtime.dispatch.mock.calls.filter(
          ([command]) => command.type === 'LOAD_SCENE',
        ),
      ).toHaveLength(3),
    );
    expect(runtime.dispatch).toHaveBeenLastCalledWith({
      type: 'LOAD_SCENE',
      scene: {
        ...queuedScene,
        camera: settledCamera,
        contributions: expectedContributions,
      },
    });
    expect(
      runtime.dispatch.mock.calls.filter(
        ([command]) => command.type === 'PATCH_CONTRIBUTION',
      ),
    ).toHaveLength(1);
  });
});

describe('System Map overlay stacking', () => {
  it('keeps the body hover card above the always-visible name layer', () => {
    // jsdom does not apply component styles, so read the rule itself: a name
    // painted over the card would make the card unreadable.
    const source = readFileSync(
      resolve(process.cwd(), 'src/lib/spatial/SpatialCanvas.svelte'),
      'utf8',
    );
    const zIndexOf = (selector: string) => {
      const rule = source.match(
        new RegExp(`\\n  ${selector.replace('.', '\\.')} \\{([^}]*)\\}`),
      );
      const match = rule?.[1]?.match(/z-index:\s*(\d+)/);
      return match ? Number(match[1]) : Number.NaN;
    };
    const labels = zIndexOf('.system-label-layer');
    const card = zIndexOf('.system-body-hover');
    expect(labels).toBeGreaterThan(0);
    expect(card).toBeGreaterThan(labels);
  });
});
