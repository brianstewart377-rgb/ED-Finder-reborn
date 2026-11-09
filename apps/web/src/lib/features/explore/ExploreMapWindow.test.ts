import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isTopmostPopout } from '$lib/components/popout-stack';
import type {
  CameraState,
  RuntimeCommand,
  RuntimeEventListener,
  SpatialRuntimeStatusListener,
} from '$lib/spatial/contracts';
import { buildGalaxyReviewFixtureScene } from '$lib/spatial/galaxy-review-fixture';
import {
  createManagedSpatialRuntime,
  type SpatialBackendResourceListener,
  type SpatialBackendSession,
} from '$lib/spatial/lifecycle';
import ExploreMapWindow from './ExploreMapWindow.svelte';
import BodyFilters from './BodyFilters.svelte';

const adapter = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('$lib/spatial/babylon/adapter', () => ({
  createBabylonSpatialRuntime: adapter.create,
}));

function managedSession(onStatus: SpatialRuntimeStatusListener) {
  const frames = new Map<number, () => void>();
  let nextFrame = 0;
  let emit: RuntimeEventListener = () => undefined;
  let resources: SpatialBackendResourceListener = () => undefined;
  const unsubscribeResources = vi.fn();
  const session = {
    backend: 'WEBGL2' as const,
    subscribeResourceEvents: vi.fn(
      (listener: SpatialBackendResourceListener) => {
        resources = listener;
        return unsubscribeResources;
      },
    ),
    execute: vi.fn(
      (
        command: Exclude<RuntimeCommand, { type: 'RESIZE' }>,
        listener: RuntimeEventListener,
      ) => {
        emit = listener;
        if (command.type === 'LOAD_SCENE' || command.type === 'SET_CAMERA') {
          listener({
            type: 'CAMERA_CHANGED',
            camera:
              command.type === 'LOAD_SCENE'
                ? command.scene.camera
                : command.camera,
          });
        }
        return { status: 'executed' as const };
      },
    ),
    render: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  } satisfies SpatialBackendSession;
  const scheduler = {
    request: vi.fn((callback: () => void) => {
      frames.set(++nextFrame, callback);
      return nextFrame;
    }),
    cancel: vi.fn((handle: number) => frames.delete(handle)),
  };
  const runtime = createManagedSpatialRuntime(
    { createWebGpu: async () => null, createWebGl2: () => session },
    onStatus,
    scheduler,
  );
  const subscribe = runtime.subscribe.bind(runtime);
  const unsubscribed = vi.fn();
  runtime.subscribe = vi.fn((listener) => {
    const unsubscribe = subscribe(listener);
    return () => {
      unsubscribed();
      unsubscribe();
    };
  });
  return {
    runtime,
    session,
    frames,
    scheduler,
    unsubscribed,
    unsubscribeResources,
    emit: (event: Parameters<RuntimeEventListener>[0]) => emit(event),
    resource: (state: 'lost' | 'recovered') =>
      resources({ state, detail: 'window-lifecycle-test' }),
  };
}

describe('Explore map', () => {
  let sessions: ReturnType<typeof managedSession>[];
  let disconnect: ReturnType<typeof vi.fn>;
  let opener: HTMLButtonElement;

  beforeEach(() => {
    sessions = [];
    disconnect = vi.fn();
    opener = document.createElement('button');
    opener.textContent = 'Search';
    document.body.append(opener);
    opener.focus();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = vi.fn();
        disconnect = disconnect;
      },
    );
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(360);
    adapter.create.mockReset().mockImplementation((_canvas, onStatus) => {
      const record = managedSession(onStatus);
      sessions.push(record);
      return record.runtime;
    });
  });

  afterEach(() => {
    cleanup();
    opener.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function ready() {
    await waitFor(() =>
      expect(document.querySelector('[data-renderer-state]')).toHaveAttribute(
        'data-renderer-state',
        'ready',
      ),
    );
    expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(1);
  }

  it('is a page region with one canvas and no window controls, and never takes focus', async () => {
    render(ExploreMapWindow, {
      canvasProps: { scene: buildGalaxyReviewFixtureScene() },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    const map = screen.getByRole('region', { name: 'Galaxy map' });
    expect(screen.queryByRole('dialog')).toBeNull();
    for (const name of [
      'Close map',
      'Expand',
      'Restore size',
      'Back to results',
      'Show Finder',
    ])
      expect(within(map).queryByRole('button', { name })).toBeNull();
    // Always on the page beside the Finder: mounting must not move focus or
    // join the pop-out stack that owns Escape.
    expect(opener).toHaveFocus();
    expect(isTopmostPopout(map)).toBe(false);
    expect(adapter.create).toHaveBeenCalledOnce();
  });

  it('names the map for the selected system', async () => {
    render(ExploreMapWindow, {
      canvasProps: { scene: buildGalaxyReviewFixtureScene() },
      selectedSystemName: 'Sol',
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    expect(
      screen.getByRole('region', { name: 'Sol · Galaxy map' }),
    ).toBeInTheDocument();
  });

  it('offers Show Finder only while the Finder panel is hidden, without rebuilding the canvas', async () => {
    const onShowFinder = vi.fn();
    const view = render(ExploreMapWindow, {
      canvasProps: { scene: buildGalaxyReviewFixtureScene() },
      finderPanelOpen: true,
      onShowFinder,
    });
    await ready();
    const map = screen.getByRole('region', { name: 'Galaxy map' });
    const canvas = map.querySelector('canvas');
    expect(map).toHaveAttribute('data-finder-panel-open', 'true');
    await view.rerender({ finderPanelOpen: false });
    const show = within(map).getByRole('button', { name: 'Show Finder' });
    expect(show).toHaveAttribute('aria-controls', 'finder-panel');
    expect(show).toHaveAttribute('aria-expanded', 'false');
    await fireEvent.click(show);
    expect(onShowFinder).toHaveBeenCalledOnce();
    await view.rerender({ finderPanelOpen: true });
    expect(
      within(map).queryByRole('button', { name: 'Show Finder' }),
    ).toBeNull();
    expect(map.querySelector('canvas')).toBe(canvas);
    expect(adapter.create).toHaveBeenCalledOnce();
    expect(sessions[0]!.session.dispose).not.toHaveBeenCalled();
  });

  it('floats camera controls, the readout and the renderer status over the map, retaining every action', async () => {
    render(ExploreMapWindow, {
      canvasProps: { scene: buildGalaxyReviewFixtureScene() },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    const canvas = screen.getByRole('application');
    const controls = screen.getByRole('group', {
      name: 'Galaxy camera controls',
    });
    expect(canvas.compareDocumentPosition(controls)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // Over the map, not stacked under it: the bar shares the map's surface,
    // as do the renderer status and the Nearby systems list.
    const surface = canvas.closest('.map-systems-surface')!;
    const bar = controls.closest('.map-overlay-bar');
    expect(bar).not.toBeNull();
    expect(surface.contains(bar)).toBe(true);
    expect(bar!.contains(screen.getByRole('status'))).toBe(true);
    expect(surface.querySelector('.nearby-systems--overlay')).not.toBeNull();
    expect(surface.closest('.map-window-content')!.lastElementChild).toBe(
      surface,
    );
    for (const name of [
      'Zoom in',
      'Zoom out',
      'Top down',
      'Tilted',
      'Reset view',
      'Rotate left',
      'Rotate right',
      'Move up',
      'Move down',
    ])
      expect(within(controls).getByRole('button', { name })).toBeEnabled();
    expect(controls).toHaveTextContent(/Range .* ly/);
    expect(controls).toHaveTextContent(/Grid /);
    expect(
      within(controls).getByRole('group', { name: 'Focus coordinates' }),
    ).toBeInTheDocument();
  });

  it('leaves Escape to the Body filters tool and keeps the map', async () => {
    render(ExploreMapWindow, {
      canvasProps: {},
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    render(BodyFilters, {
      value: {},
      onapply: vi.fn(),
      onremove: vi.fn(),
      onclear: vi.fn(),
    });
    const trigger = screen.getByRole('button', { name: 'Body filters' });
    await fireEvent.click(trigger);
    const filters = screen.getByRole('dialog', { name: 'Body filters' });
    expect(isTopmostPopout(filters)).toBe(true);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(filters).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(
      screen.getByRole('region', { name: /Galaxy map$/ }),
    ).toBeInTheDocument();
    expect(sessions[0]!.session.dispose).not.toHaveBeenCalled();
  });

  it('keeps the hover card Escape and the map after it', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    render(ExploreMapWindow, {
      canvasProps: { scene },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    sessions[0]!.emit({ type: 'TARGET_HOVERED', target: scene.selection[0] });
    const tooltip = await screen.findByRole('tooltip');
    within(tooltip).getByRole('button', { name: 'Select' }).focus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(screen.getByRole('application')).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('application')).toBeInTheDocument();
    expect(sessions[0]!.session.dispose).not.toHaveBeenCalled();
  });

  it('disposes managed GPU sessions, observers and queued frames before rebuilding retained CPU context', async () => {
    const original = buildGalaxyReviewFixtureScene();
    let retainedCamera = original.camera;
    const events = vi.fn((event: Parameters<RuntimeEventListener>[0]) => {
      if (event.type === 'CAMERA_CHANGED' && 'focusLy' in event.camera)
        retainedCamera = event.camera;
    });
    let scene = original;
    for (let cycle = 0; cycle < 3; cycle += 1) {
      const view = render(ExploreMapWindow, {
        canvasProps: { scene, onRuntimeEvent: events },
        finderPanelOpen: cycle !== 1,
        onShowFinder: vi.fn(),
      });
      await ready();
      const record = sessions[cycle]!;
      expect(
        sessions.filter(
          ({ session }) => session.dispose.mock.calls.length === 0,
        ),
      ).toHaveLength(1);
      expect(record.session.execute).toHaveBeenCalledWith(
        { type: 'LOAD_SCENE', scene },
        expect.any(Function),
      );
      expect(record.runtime.subscribe).toHaveBeenCalledOnce();
      const moved: CameraState = {
        ...retainedCamera,
        focusLy: { x: 120 + cycle, y: 48, z: -90 },
        bearingRad: 0.8,
        distanceLy: 640,
        revision: 9 + cycle,
      };
      record.emit({ type: 'CAMERA_CHANGED', camera: moved });
      view.unmount();
      expect(retainedCamera).toEqual(moved);
      expect(record.session.dispose).toHaveBeenCalledOnce();
      expect(record.unsubscribeResources).toHaveBeenCalledOnce();
      expect(record.unsubscribed).toHaveBeenCalledOnce();
      expect(record.frames.size).toBe(0);
      const previousCount = events.mock.calls.length;
      record.emit({ type: 'CAMERA_CHANGED', camera: original.camera });
      record.resource('recovered');
      expect(events).toHaveBeenCalledTimes(previousCount);
      scene = { ...original, camera: retainedCamera };
    }
    expect(adapter.create).toHaveBeenCalledTimes(3);
    expect(disconnect).toHaveBeenCalledTimes(3);
  });

  it('forwards reduced-motion camera requests without starting a flight', async () => {
    const scene = buildGalaxyReviewFixtureScene();
    const cameraRequest = { ...scene.camera, distanceLy: 600, revision: 2 };
    render(ExploreMapWindow, {
      canvasProps: {
        scene,
        cameraRequest,
        cameraRequestRevision: 1,
        focusTarget: scene.selection[0],
        focusRevision: 1,
      },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    await waitFor(() =>
      expect(sessions[0]!.session.execute).toHaveBeenCalledWith(
        {
          type: 'SET_CAMERA',
          camera: cameraRequest,
          transition: { durationMs: 650, reducedMotion: true },
        },
        expect.any(Function),
      ),
    );
    expect(sessions[0]!.session.execute).toHaveBeenCalledWith(
      { type: 'FLY_TO', target: scene.selection[0], reducedMotion: true },
      expect.any(Function),
    );
    expect(
      document.querySelector('[data-camera-transition-active]'),
    ).toHaveAttribute('data-camera-transition-active', 'false');
  });

  it('keeps the original Reset view camera through a canvas rebuild', async () => {
    const original = buildGalaxyReviewFixtureScene();
    const resetCamera = original.camera;
    const first = render(ExploreMapWindow, {
      canvasProps: { scene: original, resetCamera },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    first.unmount();
    const currentCamera = {
      ...resetCamera,
      focusLy: { x: 600, y: 50, z: 80 },
      distanceLy: 400,
      revision: 8,
    };
    render(ExploreMapWindow, {
      canvasProps: {
        scene: { ...original, camera: currentCamera },
        resetCamera,
      },
      finderPanelOpen: true,
      onShowFinder: vi.fn(),
    });
    await ready();
    await fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(sessions[1]!.session.execute).toHaveBeenCalledWith(
      {
        type: 'SET_CAMERA',
        camera: { ...resetCamera, revision: currentCamera.revision + 1 },
        transition: { durationMs: 650, reducedMotion: true },
      },
      expect.any(Function),
    );
    expect(sessions[0]!.session.dispose).toHaveBeenCalledOnce();
  });

  it.each([true, false])(
    'has no axe violations with the real canvas controls (Finder panel open: %s)',
    async (finderPanelOpen) => {
      const { container } = render(ExploreMapWindow, {
        canvasProps: { scene: buildGalaxyReviewFixtureScene() },
        finderPanelOpen,
        onShowFinder: vi.fn(),
      });
      await ready();
      const result = await axe.run(container, {
        rules: { 'color-contrast': { enabled: false } },
      });
      expect(result.violations).toEqual([]);
    },
  );
});
