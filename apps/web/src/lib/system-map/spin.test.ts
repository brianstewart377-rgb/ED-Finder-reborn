import { describe, expect, it } from 'vitest';
import { cosmosAsk } from './cosmos-look';
import { lookOf } from './draw';
import { nodeNamed, treeOf } from './recorded-systems';
import {
  SPIN_EVERY_MS,
  SPIN_FRAMES_BEFORE_JUDGING,
  SPIN_RESTS_AFTER_MS,
  SPIN_ROUND_MAX_STEPS,
  SPIN_ROUND_TOO_SLOW_MS,
  SPIN_STEP_BUDGET_MS,
  startSpinning,
  type SpinTarget,
} from './spin';

/** A clock the test moves by hand, and a stand-in for Cosmos that takes as long as it is told. */
function bench(msPerBody: number) {
  let now = 0;
  let step: ((time: number) => void) | null = null;
  const clock = {
    now: () => now,
    frame: (next: (time: number) => void) => {
      step = next;
      return 1;
    },
    cancel: () => {
      step = null;
    },
  };
  const asked: { type: string; time: number; resolution?: number }[] = [];
  const generator = {
    render: (params: { type: string; resolution?: number }, time: number) => {
      asked.push({ type: params.type, time, resolution: params.resolution });
      now += msPerBody;
      return {} as HTMLCanvasElement;
    },
  };
  /** Moves the clock on to `time` and runs the frame waiting there. */
  const tick = (time: number) => {
    now = time;
    const waiting = step;
    step = null;
    waiting?.(time);
  };
  return { clock, generator, asked, tick, running: () => step !== null };
}

function pen() {
  const drawn: number[] = [];
  return {
    drawn,
    pen: {
      clearRect: () => {},
      drawImage: () => drawn.push(1),
    } as unknown as CanvasRenderingContext2D,
  };
}

function targetsOf(names: string[]): SpinTarget[] {
  const tree = treeOf('sol');
  return names.map((name) => {
    const node = nodeNamed(tree, name);
    const look = lookOf(node);
    return {
      id: node.id,
      ask: cosmosAsk({
        family: look.family,
        star: look.star,
        colour: look.colour,
        halo: look.halo,
      }),
      seed: 1,
      pixels: 64,
      pen: pen().pen,
    };
  });
}

describe('spinning', () => {
  it('draws each body on screen twelve times a second, one step on each time', () => {
    const { clock, generator, asked, tick } = bench(0.1);
    const drew: string[] = [];
    const targets = targetsOf(['Earth', 'Jupiter']);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => 0,
        drew: (id) => drew.push(id),
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    tick(SPIN_EVERY_MS / 2); // too soon: nothing drawn
    tick(SPIN_EVERY_MS);
    tick(SPIN_EVERY_MS * 2);
    expect(asked.map((one) => one.type)).toEqual([
      'terrain_wet',
      'gas_giant_ringed',
      'terrain_wet',
      'gas_giant_ringed',
      'terrain_wet',
      'gas_giant_ringed',
    ]);
    expect(asked.map((one) => one.time)).toEqual(
      [0, 0, 1 / 12, 1 / 12, 2 / 12, 2 / 12].map((t) => expect.closeTo(t)),
    );
    expect(drew).toEqual(targets.map((target) => target.id));
  });

  it('draws bodies of one size together, so Cosmos makes its drawing surface anew as seldom as it can', () => {
    // Cosmos draws on one surface and makes it anew whenever the size asked
    // for differs from the last. Seen on a gaming PC: Maia's 56 bodies took
    // 9.7 ms a step, and spinning switched itself off.
    const { clock, generator, asked, tick } = bench(0.1);
    const sizes = [128, 32, 512, 32, 128, 64, 32, 512];
    const names = [
      'Sol',
      'Mercury',
      'Venus',
      'Earth',
      'Mars',
      'Jupiter',
      'Saturn',
      'Uranus',
    ];
    const targets = targetsOf(names).map((target, at) => ({
      ...target,
      pixels: sizes[at],
    }));
    const drew: string[] = [];
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => 0,
        drew: (id) => drew.push(id),
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    const order = asked.map((one) => one.resolution);
    expect(order).toEqual([32, 32, 32, 64, 128, 128, 512, 512]);
    // Listed as they came, the size would change seven times; now three.
    const changes = (list: (number | undefined)[]) =>
      list.filter((size, at) => at > 0 && size !== list[at - 1]).length;
    expect(changes(sizes)).toBe(7);
    expect(changes(order)).toBe(3);
    // Every body is still drawn, and the page's own list is left as it was.
    expect([...drew].sort()).toEqual(targets.map((t) => t.id).sort());
    expect(targets.map((target) => target.pixels)).toEqual(sizes);
  });

  it('rests after 20 seconds without the mouse, the wheel or a key, and carries on from where it stopped', () => {
    const { clock, generator, asked, tick } = bench(0.1);
    let stirred = 0;
    startSpinning(
      generator,
      {
        targets: () => targetsOf(['Earth']),
        lastStir: () => stirred,
        drew: () => {},
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    tick(SPIN_RESTS_AFTER_MS + 1000);
    tick(SPIN_RESTS_AFTER_MS + 2000);
    expect(asked).toHaveLength(1);
    stirred = SPIN_RESTS_AFTER_MS + 2500;
    tick(SPIN_RESTS_AFTER_MS + 3000);
    expect(asked).toHaveLength(2);
    expect(asked[1]!.time).toBeCloseTo(1 / 12);
  });

  const EIGHT = [
    'Sol',
    'Mercury',
    'Venus',
    'Earth',
    'Mars',
    'Jupiter',
    'Saturn',
    'Uranus',
  ];

  it('turns as many bodies as fit in a step, and the rest on the next steps', () => {
    // Seen on a gaming PC: Maia's 56 bodies took 8.7 to 11.1 ms a step, over
    // the limit of the day, and spinning switched itself off. A large system
    // now turns over several steps instead.
    expect(SPIN_STEP_BUDGET_MS).toBe(6);
    const { clock, generator, asked, tick, running } = bench(2.5);
    let slow: number | null = null;
    const targets = targetsOf(EIGHT);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    // 2.5 ms a body: three fit in a step's 6 ms (the third is begun at 5 ms
    // and finished), so eight bodies take three steps: 3, 3 and 2.
    const drawnBy: number[] = [];
    for (let step = 0; step < 9; step += 1) {
      tick(step * SPIN_EVERY_MS * 2);
      drawnBy.push(asked.length);
    }
    expect(drawnBy).toEqual([3, 6, 8, 11, 14, 16, 19, 22, 24]);
    // Every round draws every body once, in the same order.
    const kinds = asked.map((one) => one.type);
    expect(kinds.slice(8, 16)).toEqual(kinds.slice(0, 8));
    expect(kinds.slice(16, 24)).toEqual(kinds.slice(0, 8));
    // Each step is one step on in time, whichever bodies it draws.
    expect(asked[0].time).toBeCloseTo(0);
    expect(asked[3].time).toBeCloseTo(1 / 12);
    expect(asked[6].time).toBeCloseTo(2 / 12);
    expect(asked[8].time).toBeCloseTo(3 / 12);
    // 20 ms of drawing for a round is within what a round may take.
    expect(SPIN_ROUND_TOO_SLOW_MS).toBe(24);
    expect(slow).toBeNull();
    expect(running()).toBe(true);
  });

  it('never spends more than a step may on drawing, plus the body it had begun', () => {
    const { clock, generator, tick } = bench(2.5);
    const targets = targetsOf(EIGHT);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    for (let step = 0; step < 12; step += 1) {
      const began = step * SPIN_EVERY_MS * 2;
      tick(began);
      expect(clock.now() - began).toBeLessThanOrEqual(
        SPIN_STEP_BUDGET_MS + 2.5,
      );
    }
  });

  it('stops itself, and says how slow it was, where turning every body once takes more than four steps', () => {
    // A whole system on screen on a machine with no graphics card to speak
    // of: 5 ms a body, so 40 ms to turn the eight of them once.
    expect(SPIN_ROUND_MAX_STEPS).toBe(4);
    const { clock, generator, tick, running } = bench(5);
    let slow: number | null = null;
    const targets = targetsOf(EIGHT);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    // The first steps are not judged: the first are always the slowest.
    for (let step = 0; step < SPIN_FRAMES_BEFORE_JUDGING; step += 1)
      tick(step * SPIN_EVERY_MS * 2);
    expect(slow).toBeNull();
    expect(running()).toBe(true);
    // Then it is: it stops as soon as a round has cost too much, and says
    // what a whole round would take.
    let step = SPIN_FRAMES_BEFORE_JUDGING;
    while (running() && step < 40) {
      tick(step * SPIN_EVERY_MS * 2);
      step += 1;
    }
    expect(running()).toBe(false);
    expect(step).toBeLessThanOrEqual(SPIN_FRAMES_BEFORE_JUDGING + 4);
    expect(slow).toBeCloseTo(40);
    expect(slow!).toBeGreaterThan(SPIN_ROUND_TOO_SLOW_MS);
  });

  it('is not stopped by slow first steps, while the graphics card is still being handed its work', () => {
    let now = 0;
    let next: ((time: number) => void) | null = null;
    const clock = {
      now: () => now,
      frame: (step: (time: number) => void) => {
        next = step;
        return 1;
      },
      cancel: () => {
        next = null;
      },
    };
    let calls = 0;
    const generator = {
      render: () => {
        calls += 1;
        // The first two bodies ever drawn take 60 ms each; then 0.2 ms.
        now += calls <= 2 ? 60 : 0.2;
        return {} as HTMLCanvasElement;
      },
    };
    let slow: number | null = null;
    const targets = targetsOf(EIGHT);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    for (let step = 0; step < 30; step += 1) {
      now = step * SPIN_EVERY_MS * 2;
      const waiting = next as ((time: number) => void) | null;
      next = null;
      waiting?.(now);
    }
    expect(slow).toBeNull();
    expect(next).not.toBeNull();
  });

  it('carries on where a step is cheap', () => {
    const { clock, generator, tick, running } = bench(1);
    let slow: number | null = null;
    const targets = targetsOf(['Earth', 'Jupiter']);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    for (let frame = 0; frame < SPIN_FRAMES_BEFORE_JUDGING * 3; frame += 1)
      tick(frame * SPIN_EVERY_MS * 2);
    expect(slow).toBeNull();
    expect(running()).toBe(true);
  });

  it('stops, leaving the still pictures, when Cosmos cannot go on', () => {
    const { clock, tick, running } = bench(0);
    let failed = false;
    startSpinning(
      {
        render: () => {
          throw new Error('context lost');
        },
      },
      {
        targets: () => targetsOf(['Earth']),
        lastStir: () => 0,
        drew: () => {},
        tooSlow: () => {},
        failed: () => (failed = true),
      },
      clock,
    );
    tick(0);
    expect(failed).toBe(true);
    expect(running()).toBe(false);
  });
});
