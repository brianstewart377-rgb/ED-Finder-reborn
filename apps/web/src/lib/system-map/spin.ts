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
 *   - a step spends about 6 ms drawing at most, well under a tenth of the
 *     time between steps. The bodies that do not fit turn on the next step:
 *     a large system turns a little less smoothly, where at first it did
 *     not turn at all (Maia's 56 bodies took 8.7 to 11.1 ms a step on a
 *     gaming PC, over the limit of the day, and spinning switched itself
 *     off);
 *   - where turning every body on screen once takes more than four steps'
 *     worth of drawing (no graphics card to speak of), spinning stops
 *     itself and the still pictures stay;
 *   - bodies of one size are drawn together. Cosmos draws every body on one
 *     surface and makes that surface anew whenever the size asked for
 *     differs from the last, which costs more than the drawing. A system's
 *     bodies come in five sizes, mixed in the order they are listed: taken
 *     in that order, nearly every body paid for a new surface.
 */
import type { CelestialGenerator, CelestialType } from '@eluvade/cosmos';
import type { CosmosAsk } from './cosmos-look';

/** Twelve times a second: the bodies turn slowly, so more would not show. */
export const SPIN_EVERY_MS = 1000 / 12;
/** How long without the mouse, the wheel or a key before the bodies rest. */
export const SPIN_RESTS_AFTER_MS = 20_000;
/**
 * How long a step may spend drawing before it leaves the rest of the bodies
 * to the next step. A body once begun is finished, so a step can run over
 * by one body.
 */
export const SPIN_STEP_BUDGET_MS = 6;
/** Every body on screen is to be turned once within this many steps. */
export const SPIN_ROUND_MAX_STEPS = 4;
/**
 * The most drawing that turning every body on screen once may take. Where
 * it takes more, each body would turn less than three times a second, and
 * spinning stops itself.
 */
export const SPIN_ROUND_TOO_SLOW_MS =
  SPIN_STEP_BUDGET_MS * SPIN_ROUND_MAX_STEPS;
/**
 * How many steps are drawn before the speed is judged. The first are the
 * slowest (the graphics card is still being handed its work), and what they
 * cost is left out of the judging.
 */
export const SPIN_FRAMES_BEFORE_JUDGING = 5;

export type SpinTarget = Readonly<{
  id: string;
  ask: CosmosAsk;
  seed: number;
  /** How many pixels across: the same as the body's still picture. */
  pixels: number;
  pen: CanvasRenderingContext2D;
}>;

export type SpinHooks = Readonly<{
  /** The bodies to draw this frame: those on screen. */
  targets: () => readonly SpinTarget[];
  /** When the person last moved the mouse or the wheel or pressed a key. */
  lastStir: () => number;
  /** A body's moving picture has been drawn for the first time. */
  drew: (id: string) => void;
  /**
   * Spinning stopped itself: turning every body on screen once takes
   * `msARound` of drawing (worked out from the bodies drawn so far, where it
   * stopped before the end of a round).
   */
  tooSlow: (msARound: number) => void;
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
  const seen = new Set<string>();
  // One round turns every body on screen once. It takes one step where the
  // bodies fit in a step's time, and several where they do not.
  let waiting: SpinTarget[] = [];
  let inRound = 0;
  // What the judged steps of this round have drawn, and what it cost.
  let judgedBodies = 0;
  let judgedMs = 0;

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
    if (waiting.length === 0) {
      // A new round: the bodies on screen now, smallest first, those of one
      // size together (see the note above).
      waiting = [...hooks.targets()].sort(
        (one, other) => one.pixels - other.pixels,
      );
      inRound = waiting.length;
      judgedBodies = 0;
      judgedMs = 0;
    }
    if (waiting.length === 0) return;
    let drawnNow = 0;
    try {
      while (waiting.length > 0) {
        const target = waiting.shift()!;
        const drawn = generator.render(
          {
            type: target.ask.kind as CelestialType,
            seed: target.seed,
            resolution: target.pixels,
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
        target.pen.clearRect(0, 0, target.pixels, target.pixels);
        target.pen.drawImage(drawn, 0, 0);
        drawnNow += 1;
        if (!seen.has(target.id)) {
          seen.add(target.id);
          hooks.drew(target.id);
        }
        // The rest turn on the next step.
        if (clock.now() - started >= SPIN_STEP_BUDGET_MS) break;
      }
    } catch {
      stop();
      hooks.failed();
      return;
    }
    const judged = frames >= SPIN_FRAMES_BEFORE_JUDGING;
    frames += 1;
    if (!judged) return;
    judgedBodies += drawnNow;
    judgedMs += clock.now() - started;
    // As soon as a round has cost too much, without waiting for its end:
    // on a machine that slow, the end is a long way off.
    if (judgedMs > SPIN_ROUND_TOO_SLOW_MS) {
      stop();
      hooks.tooSlow(Math.round((judgedMs / judgedBodies) * inRound * 10) / 10);
    }
  };

  handle = clock.frame(turn);
  return stop;
}
