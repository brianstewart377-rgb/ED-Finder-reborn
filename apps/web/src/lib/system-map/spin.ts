/**
 * Spinning bodies on the System Map. Cosmos can draw a body as it is a moment
 * later, so planets turn and stars seethe. Each body's still picture stays in
 * place; a small canvas laid over it is drawn afresh twelve times a second,
 * all from Cosmos's one drawing surface.
 *
 * Spinning is a nicety and must cost next to nothing (the owner, 14 November
 * 2026: "only if it's negliable"). So:
 *   - only bodies on screen are drawn;
 *   - nothing is drawn while nobody is using the page: after 20 seconds
 *     without the mouse, the wheel or a key, the bodies rest until the next
 *     movement, and carry on from where they stopped;
 *   - a frame may take 8 ms on average, a tenth of the time between frames.
 *     Where it takes more (no graphics card to speak of, or a very large
 *     system on screen), spinning stops itself and the still pictures stay.
 */
import type { CelestialGenerator, CelestialType } from '@eluvade/cosmos';
import type { CosmosAsk } from './cosmos-look';

/** How many pixels across each moving picture is drawn. */
export const SPIN_PIXELS = 256;
/** Twelve times a second: the bodies turn slowly, so more would not show. */
export const SPIN_EVERY_MS = 1000 / 12;
/** How long without the mouse, the wheel or a key before the bodies rest. */
export const SPIN_RESTS_AFTER_MS = 20_000;
/** The most a frame may take on average before spinning stops itself. */
export const SPIN_TOO_SLOW_MS = 8;
/** How many frames are drawn before the average is judged. */
export const SPIN_FRAMES_BEFORE_JUDGING = 5;

export type SpinTarget = Readonly<{
  id: string;
  ask: CosmosAsk;
  seed: number;
  pen: CanvasRenderingContext2D;
}>;

export type SpinHooks = Readonly<{
  /** The bodies to draw this frame: those on screen. */
  targets: () => readonly SpinTarget[];
  /** When the person last moved the mouse or the wheel or pressed a key. */
  lastStir: () => number;
  /** A body's moving picture has been drawn for the first time. */
  drew: (id: string) => void;
  /** Spinning stopped itself: frames took `msEach` on average. */
  tooSlow: (msEach: number) => void;
  /** Cosmos could not go on (the graphics card was taken away, say). */
  failed: () => void;
}>;

export type SpinClock = Readonly<{
  now: () => number;
  frame: (step: (time: number) => void) => number;
  cancel: (handle: number) => void;
}>;

const browserClock: SpinClock = {
  now: () => performance.now(),
  frame: (step) => requestAnimationFrame(step),
  cancel: (handle) => cancelAnimationFrame(handle),
};

/** Starts the bodies turning. Returns what stops them. */
export function startSpinning(
  generator: Pick<CelestialGenerator, 'render'>,
  hooks: SpinHooks,
  clock: SpinClock = browserClock,
): () => void {
  let handle = 0;
  let stopped = false;
  let last = -Infinity;
  let frames = 0;
  let spent = 0;
  const seen = new Set<string>();

  const stop = () => {
    stopped = true;
    clock.cancel(handle);
  };

  const turn = (time: number) => {
    if (stopped) return;
    handle = clock.frame(turn);
    if (time - last < SPIN_EVERY_MS) return;
    if (frames > 0 && clock.now() - hooks.lastStir() > SPIN_RESTS_AFTER_MS)
      return;
    last = time;
    const started = clock.now();
    // Each frame is one step on, so the bodies do not jump after a rest.
    const seconds = (frames * SPIN_EVERY_MS) / 1000;
    const targets = hooks.targets();
    try {
      for (const target of targets) {
        const drawn = generator.render(
          {
            type: target.ask.kind as CelestialType,
            seed: target.seed,
            resolution: SPIN_PIXELS,
            ...(target.ask.colours
              ? {
                  colors: target.ask.colours as Record<
                    string,
                    [number, number, number, number]
                  >,
                }
              : {}),
          },
          seconds,
        );
        target.pen.clearRect(0, 0, SPIN_PIXELS, SPIN_PIXELS);
        target.pen.drawImage(drawn, 0, 0);
        if (!seen.has(target.id)) {
          seen.add(target.id);
          hooks.drew(target.id);
        }
      }
    } catch {
      stop();
      hooks.failed();
      return;
    }
    if (targets.length === 0) return;
    frames += 1;
    spent += clock.now() - started;
    const msEach = spent / frames;
    if (frames >= SPIN_FRAMES_BEFORE_JUDGING && msEach > SPIN_TOO_SLOW_MS) {
      stop();
      hooks.tooSlow(Math.round(msEach * 10) / 10);
    }
  };

  handle = clock.frame(turn);
  return stop;
}
