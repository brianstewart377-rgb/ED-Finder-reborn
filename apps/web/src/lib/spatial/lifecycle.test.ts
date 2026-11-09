import { describe, expect, it, vi } from 'vitest';

import type {
  RuntimeCommand,
  RuntimeEvent,
  SpatialRendererBackend,
  SpatialViewport,
} from './contracts';
import {
  createManagedSpatialRuntime,
  type SpatialBackendResourceEvent,
  type SpatialBackendResourceListener,
  type SpatialBackendSession,
  type SpatialFrameScheduler,
} from './lifecycle';

const viewport: SpatialViewport = { width: 640, height: 360, dpr: 2 };
const widerViewport: SpatialViewport = { width: 800, height: 450, dpr: 1 };

const createFrameScheduler = () => {
  let nextHandle = 1;
  const callbacks = new Map<number, () => void>();
  const scheduler: SpatialFrameScheduler = {
    request: vi.fn((callback) => {
      const handle = nextHandle++;
      callbacks.set(handle, callback);
      return handle;
    }),
    cancel: vi.fn(),
  };

  return {
    scheduler,
    run(handle: number) {
      callbacks.get(handle)?.();
    },
  };
};

const createSession = (backend: SpatialRendererBackend) => {
  let resourceListener: SpatialBackendResourceListener | null = null;
  const unsubscribeResourceEvents = vi.fn();
  return {
    backend,
    subscribeResourceEvents: vi.fn(
      (listener: SpatialBackendResourceListener) => {
        resourceListener = listener;
        return unsubscribeResourceEvents;
      },
    ),
    resize: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    unsubscribeResourceEvents,
    emitResource(event: SpatialBackendResourceEvent) {
      resourceListener?.(event);
    },
  } satisfies SpatialBackendSession & {
    unsubscribeResourceEvents: ReturnType<typeof vi.fn>;
    emitResource(event: SpatialBackendResourceEvent): void;
  };
};

describe('renderer-neutral spatial runtime lifecycle', () => {
  it('prefers WebGPU and renders a pending startup resize on the next frame', async () => {
    const frames = createFrameScheduler();
    const webGpu = createSession('WEBGPU');
    const createWebGl2 = vi.fn();
    const statuses = vi.fn();
    const events: RuntimeEvent[] = [];
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2,
      },
      statuses,
      frames.scheduler,
    );
    runtime.subscribe((event) => events.push(event));

    runtime.resize(viewport);
    await expect(runtime.start()).resolves.toEqual({
      state: 'ready',
      backend: 'WEBGPU',
    });
    await runtime.start();

    expect(createWebGl2).not.toHaveBeenCalled();
    expect(webGpu.resize).toHaveBeenCalledOnce();
    expect(webGpu.resize).toHaveBeenCalledWith(viewport);
    expect(webGpu.render).not.toHaveBeenCalled();
    expect(frames.scheduler.request).toHaveBeenCalledOnce();
    expect(events).toEqual([{ type: 'READY', backend: 'WEBGPU' }]);
    expect(statuses.mock.calls.map(([status]) => status.state)).toEqual([
      'starting',
      'ready',
    ]);

    frames.run(1);
    expect(webGpu.render).toHaveBeenCalledOnce();

    runtime.dispose();
    runtime.dispose();
    expect(webGpu.unsubscribeResourceEvents).toHaveBeenCalledOnce();
    expect(webGpu.dispose).toHaveBeenCalledOnce();
    expect(runtime.getStatus()).toEqual({ state: 'disposed' });
  });

  it('preserves an immediate initial render when startup has no resize', async () => {
    const frames = createFrameScheduler();
    const webGl2 = createSession('WEBGL2');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(null),
        createWebGl2: vi.fn().mockReturnValue(webGl2),
      },
      vi.fn(),
      frames.scheduler,
    );

    await expect(runtime.start()).resolves.toEqual({
      state: 'ready',
      backend: 'WEBGL2',
    });
    expect(webGl2.render).toHaveBeenCalledOnce();
    expect(frames.scheduler.request).not.toHaveBeenCalled();
  });

  it('continues a bounded backend-requested presentation warmup', async () => {
    const frames = createFrameScheduler();
    const webGpu = createSession('WEBGPU');
    webGpu.render
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(false);
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2: vi.fn(),
      },
      vi.fn(),
      frames.scheduler,
    );

    await runtime.start();
    expect(webGpu.render).toHaveBeenCalledOnce();
    expect(frames.scheduler.request).toHaveBeenCalledOnce();

    frames.run(1);
    expect(webGpu.render).toHaveBeenCalledTimes(2);
    expect(frames.scheduler.request).toHaveBeenCalledTimes(2);

    frames.run(2);
    expect(webGpu.render).toHaveBeenCalledTimes(3);
    expect(frames.scheduler.request).toHaveBeenCalledTimes(2);
  });

  it('falls back to WebGL2 after a WebGPU initialization failure', async () => {
    const webGl2 = createSession('WEBGL2');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockRejectedValue(new Error('device lost')),
        createWebGl2: vi.fn().mockReturnValue(webGl2),
      },
      vi.fn(),
    );

    await expect(runtime.start()).resolves.toEqual({
      state: 'ready',
      backend: 'WEBGL2',
    });
    expect(webGl2.render).toHaveBeenCalledOnce();
  });

  it('reports only a bounded failure when no supported backend exists', async () => {
    const statuses = vi.fn();
    const events = vi.fn();
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(null),
        createWebGl2: vi.fn().mockReturnValue(null),
      },
      statuses,
    );
    runtime.subscribe(events);

    await expect(runtime.start()).resolves.toEqual({
      state: 'failed',
      failure: 'BACKEND_UNAVAILABLE',
    });
    expect(statuses).toHaveBeenLastCalledWith({
      state: 'failed',
      failure: 'BACKEND_UNAVAILABLE',
    });
    expect(events).not.toHaveBeenCalled();
  });

  it('disposes a backend that resolves after the runtime was disposed', async () => {
    let resolveWebGpu!: (session: SpatialBackendSession) => void;
    const lateSession = createSession('WEBGPU');
    const createWebGpu = vi.fn(
      () =>
        new Promise<SpatialBackendSession>((resolve) => {
          resolveWebGpu = resolve;
        }),
    );
    const statuses = vi.fn();
    const events = vi.fn();
    const runtime = createManagedSpatialRuntime(
      { createWebGpu, createWebGl2: vi.fn() },
      statuses,
    );
    runtime.subscribe(events);

    const start = runtime.start();
    runtime.dispose();
    resolveWebGpu(lateSession);

    await expect(start).resolves.toEqual({ state: 'disposed' });
    expect(lateSession.dispose).toHaveBeenCalledOnce();
    expect(lateSession.render).not.toHaveBeenCalled();
    expect(statuses).not.toHaveBeenCalledWith(
      expect.objectContaining({ state: 'ready' }),
    );
    expect(events).not.toHaveBeenCalled();
  });

  it('defers and coalesces resize renders through legacy and command APIs', async () => {
    const frames = createFrameScheduler();
    const webGpu = createSession('WEBGPU');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2: vi.fn(),
      },
      vi.fn(),
      frames.scheduler,
    );
    await runtime.start();

    runtime.resize(viewport);
    expect(runtime.dispatch({ type: 'RESIZE', ...widerViewport })).toEqual({
      status: 'executed',
    });

    expect(webGpu.resize).toHaveBeenCalledTimes(2);
    expect(webGpu.resize).toHaveBeenLastCalledWith(widerViewport);
    expect(webGpu.render).toHaveBeenCalledOnce();
    expect(frames.scheduler.request).toHaveBeenCalledOnce();

    frames.run(1);
    expect(webGpu.render).toHaveBeenCalledTimes(2);
  });

  it('cancels a queued resize render and rejects stale callbacks on dispose', async () => {
    const frames = createFrameScheduler();
    const webGl2 = createSession('WEBGL2');
    const events = vi.fn();
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(null),
        createWebGl2: vi.fn().mockReturnValue(webGl2),
      },
      vi.fn(),
      frames.scheduler,
    );
    runtime.subscribe(events);
    await runtime.start();
    expect(webGl2.render).toHaveBeenCalledOnce();

    runtime.resize(viewport);
    runtime.dispose();
    expect(frames.scheduler.cancel).toHaveBeenCalledWith(1);
    expect(webGl2.dispose).toHaveBeenCalledOnce();

    frames.run(1);
    webGl2.emitResource({ state: 'recovered', detail: 'late-restored' });
    expect(webGl2.render).toHaveBeenCalledOnce();
    expect(events).toHaveBeenCalledOnce();
    expect(events).toHaveBeenCalledWith({
      type: 'READY',
      backend: 'WEBGL2',
    });
  });

  it('cancels queued work when a later resize fails', async () => {
    const frames = createFrameScheduler();
    const webGl2 = createSession('WEBGL2');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(null),
        createWebGl2: vi.fn().mockReturnValue(webGl2),
      },
      vi.fn(),
      frames.scheduler,
    );
    await runtime.start();
    runtime.resize(viewport);
    webGl2.resize.mockImplementationOnce(() => {
      throw new Error('context lost');
    });

    expect(runtime.dispatch({ type: 'RESIZE', ...widerViewport })).toEqual({
      status: 'ignored',
      reason: 'inactive',
    });

    expect(runtime.getStatus()).toEqual({
      state: 'failed',
      failure: 'RUNTIME_FAILED',
    });
    expect(frames.scheduler.cancel).toHaveBeenCalledWith(1);
    expect(webGl2.unsubscribeResourceEvents).toHaveBeenCalledOnce();
    expect(webGl2.dispose).toHaveBeenCalledOnce();
    frames.run(1);
    expect(webGl2.render).toHaveBeenCalledOnce();
  });

  it('contains a queued render failure and releases the active backend', async () => {
    const frames = createFrameScheduler();
    const webGpu = createSession('WEBGPU');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2: vi.fn(),
      },
      vi.fn(),
      frames.scheduler,
    );
    await runtime.start();
    runtime.resize(viewport);
    webGpu.render.mockImplementationOnce(() => {
      throw new Error('submission failed');
    });

    frames.run(1);

    expect(runtime.getStatus()).toEqual({
      state: 'failed',
      failure: 'RUNTIME_FAILED',
    });
    expect(webGpu.unsubscribeResourceEvents).toHaveBeenCalledOnce();
    expect(webGpu.dispose).toHaveBeenCalledOnce();
  });

  it.each([
    'LOAD_SCENE',
    'PATCH_CONTRIBUTION',
    'SET_CAMERA',
    'FLY_TO',
    'HOVER',
    'CLEAR_HOVER',
    'PICK',
    'REBUILD_RESOURCES',
  ] as const)('reports %s as explicitly unsupported', (type) => {
    const runtime = createManagedSpatialRuntime(
      { createWebGpu: vi.fn(), createWebGl2: vi.fn() },
      vi.fn(),
    );

    expect(runtime.dispatch({ type } as RuntimeCommand)).toEqual({
      status: 'unsupported',
      command: type,
    });
  });

  it('forwards only backend-supported product commands and relays product events', async () => {
    const webGl2 = createSession('WEBGL2');
    const events: RuntimeEvent[] = [];
    const execute: NonNullable<SpatialBackendSession['execute']> = vi.fn(
      (command, emit) => {
        if (command.type === 'PICK') {
          emit({
            type: 'TARGET_PICKED',
            target: { kind: 'system', systemId64: '9007199254740993' },
          });
          return { status: 'executed' as const };
        }
        if (command.type === 'LOAD_SCENE' || command.type === 'FLY_TO') {
          return { status: 'executed' as const };
        }
        return { status: 'unsupported' as const, command: command.type };
      },
    );
    const productSession: SpatialBackendSession = { ...webGl2, execute };
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(null),
        createWebGl2: vi.fn().mockReturnValue(productSession),
      },
      vi.fn(),
    );
    runtime.subscribe((event) => events.push(event));
    await runtime.start();

    expect(
      runtime.dispatch({ type: 'PICK', screenX: 10, screenY: 20 }),
    ).toEqual({
      status: 'executed',
    });
    expect(events.at(-1)).toEqual({
      type: 'TARGET_PICKED',
      target: { kind: 'system', systemId64: '9007199254740993' },
    });
    expect(
      runtime.dispatch({
        type: 'REBUILD_RESOURCES',
        reason: 'context-loss',
      }),
    ).toEqual({ status: 'unsupported', command: 'REBUILD_RESOURCES' });
  });

  it('subscribes in order and cleans listeners up deterministically', async () => {
    const webGpu = createSession('WEBGPU');
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2: vi.fn(),
      },
      vi.fn(),
    );
    const calls: string[] = [];
    const unsubscribeFirst = runtime.subscribe(() => calls.push('first'));
    runtime.subscribe(() => calls.push('second'));

    await runtime.start();
    expect(calls).toEqual(['first', 'second']);

    unsubscribeFirst();
    unsubscribeFirst();
    webGpu.emitResource({ state: 'lost', detail: 'device-lost' });
    expect(calls).toEqual(['first', 'second', 'second']);

    runtime.dispose();
    webGpu.emitResource({ state: 'recovered', detail: 'device-restored' });
    expect(calls).toEqual(['first', 'second', 'second']);
  });

  it('forwards genuine resource loss and recovery and renders after recovery', async () => {
    const frames = createFrameScheduler();
    const webGpu = createSession('WEBGPU');
    const events: RuntimeEvent[] = [];
    const runtime = createManagedSpatialRuntime(
      {
        createWebGpu: vi.fn().mockResolvedValue(webGpu),
        createWebGl2: vi.fn(),
      },
      vi.fn(),
      frames.scheduler,
    );
    runtime.subscribe((event) => events.push(event));
    await runtime.start();
    runtime.resize(viewport);

    webGpu.emitResource({ state: 'lost', detail: 'webgpu-device-lost' });
    expect(frames.scheduler.cancel).toHaveBeenCalledWith(1);
    expect(webGpu.render).toHaveBeenCalledOnce();
    expect(events).toEqual([
      { type: 'READY', backend: 'WEBGPU' },
      { type: 'RESOURCE_LOST', detail: 'webgpu-device-lost' },
    ]);

    webGpu.emitResource({
      state: 'recovered',
      detail: 'webgpu-device-restored',
    });
    expect(webGpu.resize).toHaveBeenLastCalledWith(viewport);
    expect(events).toEqual([
      { type: 'READY', backend: 'WEBGPU' },
      { type: 'RESOURCE_LOST', detail: 'webgpu-device-lost' },
      { type: 'RECOVERED', detail: 'webgpu-device-restored' },
    ]);
    expect(frames.scheduler.request).toHaveBeenCalledTimes(2);

    frames.run(1);
    expect(webGpu.render).toHaveBeenCalledOnce();
    frames.run(2);
    expect(webGpu.render).toHaveBeenCalledTimes(2);
    webGpu.emitResource({
      state: 'recovered',
      detail: 'duplicate-restored',
    });
    expect(events).toHaveLength(3);
  });
});
