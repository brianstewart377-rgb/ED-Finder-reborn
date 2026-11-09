import type {
  RuntimeCommand,
  RuntimeCommandDispatchResult,
  RuntimeEvent,
  RuntimeEventListener,
  SpatialRendererBackend,
  SpatialRuntime,
  SpatialRuntimeStatus,
  SpatialRuntimeStatusListener,
  SpatialViewport,
} from './contracts';

export type SpatialBackendResourceEvent = Readonly<{
  state: 'lost' | 'recovered';
  detail: string;
}>;

export type SpatialBackendResourceListener = (
  event: SpatialBackendResourceEvent,
) => void;

export interface SpatialBackendSession {
  readonly backend: SpatialRendererBackend;
  subscribeResourceEvents?(
    listener: SpatialBackendResourceListener,
  ): () => void;
  execute?(
    command: Exclude<RuntimeCommand, { type: 'RESIZE' }>,
    emit: (event: RuntimeEvent) => void,
  ): RuntimeCommandDispatchResult;
  resize(viewport: SpatialViewport): void;
  /** Return true when the backend needs another animation frame to present. */
  render(): boolean | void;
  dispose(): void;
}

export interface SpatialBackendCandidates {
  createWebGpu(): Promise<SpatialBackendSession | null>;
  createWebGl2(): SpatialBackendSession | null;
}

export interface SpatialFrameScheduler {
  request(callback: () => void): number;
  cancel(handle: number): void;
}

const browserFrameScheduler: SpatialFrameScheduler = {
  request: (callback) => window.requestAnimationFrame(callback),
  cancel: (handle) => window.cancelAnimationFrame(handle),
};

const safeDispose = (session: SpatialBackendSession | null): void => {
  try {
    session?.dispose();
  } catch {
    // Disposal must remain best-effort and must never surface during teardown.
  }
};

const safeUnsubscribe = (unsubscribe: (() => void) | null): void => {
  try {
    unsubscribe?.();
  } catch {
    // Teardown must remain best-effort even if a backend observer misbehaves.
  }
};

export function createManagedSpatialRuntime(
  candidates: SpatialBackendCandidates,
  onStatus: SpatialRuntimeStatusListener,
  frameScheduler: SpatialFrameScheduler = browserFrameScheduler,
): SpatialRuntime {
  let status: SpatialRuntimeStatus = { state: 'created' };
  let session: SpatialBackendSession | null = null;
  let pendingStart: Promise<SpatialRuntimeStatus> | null = null;
  let pendingViewport: SpatialViewport | null = null;
  let pendingFrame: number | null = null;
  let frameGeneration = 0;
  let unsubscribeFromResources: (() => void) | null = null;
  let resourceLost = false;
  const eventListeners = new Set<RuntimeEventListener>();

  const publish = (next: SpatialRuntimeStatus): SpatialRuntimeStatus => {
    status = next;
    onStatus(next);
    return next;
  };

  const emit = (event: RuntimeEvent): void => {
    for (const listener of [...eventListeners]) {
      try {
        listener(event);
      } catch (error) {
        // A consumer cannot corrupt renderer lifecycle state, but its failure
        // remains visible to browser diagnostics.
        console.error('Spatial runtime event listener failed', error);
      }
    }
  };

  const cancelPendingFrame = (): void => {
    if (pendingFrame === null) return;
    frameScheduler.cancel(pendingFrame);
    pendingFrame = null;
    frameGeneration += 1;
  };

  const releaseSession = (): void => {
    cancelPendingFrame();
    safeUnsubscribe(unsubscribeFromResources);
    unsubscribeFromResources = null;
    const currentSession = session;
    session = null;
    safeDispose(currentSession);
  };

  const fail = (
    failure: 'INITIALIZATION_FAILED' | 'RUNTIME_FAILED',
  ): SpatialRuntimeStatus => {
    releaseSession();
    return publish({ state: 'failed', failure });
  };

  const renderOnNextFrame = (): void => {
    if (
      pendingFrame !== null ||
      !session ||
      resourceLost ||
      status.state === 'disposed' ||
      status.state === 'failed'
    ) {
      return;
    }

    const scheduledSession = session;
    const scheduledGeneration = ++frameGeneration;
    pendingFrame = frameScheduler.request(() => {
      if (scheduledGeneration !== frameGeneration) return;
      pendingFrame = null;
      if (
        session !== scheduledSession ||
        resourceLost ||
        status.state === 'disposed' ||
        status.state === 'failed'
      ) {
        return;
      }
      try {
        const needsAnotherFrame = scheduledSession.render() === true;
        if (needsAnotherFrame) renderOnNextFrame();
      } catch {
        fail('RUNTIME_FAILED');
      }
    });
  };

  const handleResourceEvent = (
    activeSession: SpatialBackendSession,
    event: SpatialBackendResourceEvent,
  ): void => {
    if (
      session !== activeSession ||
      status.state === 'disposed' ||
      status.state === 'failed'
    ) {
      return;
    }

    if (event.state === 'lost') {
      if (resourceLost) return;
      resourceLost = true;
      cancelPendingFrame();
      emit({ type: 'RESOURCE_LOST', detail: event.detail });
      return;
    }

    if (!resourceLost) return;
    resourceLost = false;
    emit({ type: 'RECOVERED', detail: event.detail });
    try {
      if (pendingViewport) activeSession.resize(pendingViewport);
      renderOnNextFrame();
    } catch {
      fail('RUNTIME_FAILED');
    }
  };

  const applyResize = (
    viewport: SpatialViewport,
  ): RuntimeCommandDispatchResult => {
    if (status.state === 'disposed' || status.state === 'failed') {
      return { status: 'ignored', reason: 'inactive' };
    }
    pendingViewport = viewport;
    if (!session || resourceLost) return { status: 'executed' };
    try {
      session.resize(viewport);
      renderOnNextFrame();
      return { status: 'executed' };
    } catch {
      fail('RUNTIME_FAILED');
      return { status: 'ignored', reason: 'inactive' };
    }
  };

  // Status can change while an awaited backend initialization is pending.
  const wasDisposed = (): boolean => status.state === 'disposed';

  const start = (): Promise<SpatialRuntimeStatus> => {
    if (status.state === 'disposed' || status.state === 'ready') {
      return Promise.resolve(status);
    }
    if (status.state === 'failed') {
      return Promise.resolve(status);
    }
    if (pendingStart) return pendingStart;

    publish({ state: 'starting' });
    pendingStart = (async () => {
      let candidate: SpatialBackendSession | null = null;

      try {
        candidate = await candidates.createWebGpu();
      } catch {
        // WebGPU initialization failure deterministically selects WebGL2.
      }

      if (wasDisposed()) {
        safeDispose(candidate);
        return status;
      }

      if (!candidate) {
        try {
          candidate = candidates.createWebGl2();
        } catch {
          return fail('INITIALIZATION_FAILED');
        }
      }

      if (wasDisposed()) {
        safeDispose(candidate);
        return status;
      }

      if (!candidate) {
        return publish({ state: 'failed', failure: 'BACKEND_UNAVAILABLE' });
      }

      session = candidate;
      try {
        resourceLost = false;
        if (session.subscribeResourceEvents) {
          const activeSession = session;
          unsubscribeFromResources = session.subscribeResourceEvents((event) =>
            handleResourceEvent(activeSession, event),
          );
        }
        if (pendingViewport) {
          session.resize(pendingViewport);
          renderOnNextFrame();
        } else {
          const needsAnotherFrame = session.render() === true;
          if (needsAnotherFrame) renderOnNextFrame();
        }
      } catch {
        return fail('INITIALIZATION_FAILED');
      }

      const backend = session.backend;
      const ready = publish({ state: 'ready', backend });
      emit({ type: 'READY', backend });
      return ready;
    })();

    return pendingStart;
  };

  return {
    getStatus: () => status,
    start,
    dispatch(command: RuntimeCommand): RuntimeCommandDispatchResult {
      if (command.type === 'RESIZE') {
        return applyResize({
          width: command.width,
          height: command.height,
          dpr: command.dpr,
        });
      }
      if (session?.execute && status.state === 'ready' && !resourceLost) {
        try {
          const result = session.execute(command, emit);
          if (result.status === 'executed') renderOnNextFrame();
          return result;
        } catch {
          fail('RUNTIME_FAILED');
          return { status: 'ignored', reason: 'inactive' };
        }
      }
      return { status: 'unsupported', command: command.type };
    },
    subscribe(listener: RuntimeEventListener) {
      if (status.state === 'disposed') return () => undefined;
      eventListeners.add(listener);
      let subscribed = true;
      return () => {
        if (!subscribed) return;
        subscribed = false;
        eventListeners.delete(listener);
      };
    },
    resize(viewport) {
      applyResize(viewport);
    },
    dispose() {
      if (status.state === 'disposed') return;
      releaseSession();
      publish({ state: 'disposed' });
      eventListeners.clear();
    },
  };
}
